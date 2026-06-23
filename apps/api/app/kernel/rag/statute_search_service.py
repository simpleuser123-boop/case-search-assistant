"""E5-3 内核法条检索服务（内部能力层，纯 Python import 面，不接 HTTP 端点）。

E 系列多产品生态 E5 第三步：在 ``app.kernel.rag`` 增加内部**法条检索能力**，对标
E3 ``InternalSearchService`` 的边界（依赖注入、纯转换、零正文泄露）：

    查询文本 / SearchProfile        -> search_statutes(...)    -> StatuteRef[]（带 text_id 锚点）
    一条 CandidateRef / case_id     -> statutes_by_case(...)   -> StatuteRef[]（类案→法条互跳）
    一条 StatuteRef / statute_id    -> cases_by_statute(...)   -> CandidateRef[]（法条→类案互跳）

第一性约束（文档 20 §1/§2/§3 + E5-1 红线，本模块严格遵守）：
- 法条条文只来自 E5-2 法条语料、带 ``text_id`` 锚点；服务**不生成/改写/续写**任何条文。
  StatuteRef 一律经 ``sanitize_statute_ref`` 收敛白名单；命中无锚点 -> fail-closed 丢弃并
  记 ``STATUTE_REF_DROPPED_NO_ANCHOR``，绝不展示无来源条文。
- 互跳只走契约对象：法条→类案出 ``CandidateRef``（复用 E3 ``sanitize_candidate_ref`` 同款
  白名单 + 锚点校验），类案→法条出 ``StatuteRef``；两侧都不携带对侧正文。
- 经各 RAG 子包/护栏公开面消费契约与查询规范化，不深引 retrieval/rerank/summary 私有；
  **不从 app.kernel.rag 聚合 __init__ 回引**（与 E3 服务同款循环导入规避：走子包路径）。
- 行为对既有检索零影响：不改 ``InternalSearchService.execute/search_candidate_refs``、
  不改 ``/api/search`` 主路径；法条检索是新增并行能力，案件检索路径逐位不变。
- 纯能力层：不接任何端点、不依赖 ``ENABLE_STATUTE_SEARCH`` 等业务 flag 的 on 路径
  （flag 门控在 E5-4 端点层做）。日志只写 query_session_id / input_hash / 计数 /
  degraded_reasons / dropped 计数，绝不写 query_text / 原始案情 / 裁判正文 / 法条以外正文。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

from app.core.logging import logger

# 经 guardrails 护栏公开面消费 StatuteRef 契约（E5-1），不本地另写白名单。
from app.kernel.guardrails.contracts import (
    ContractViolationError,
    StatuteRef,
    sanitize_statute_ref,
)

# 经 rag 子包公开面消费 E3 契约与查询规范化（不从聚合 __init__ 回引，规避循环导入）。
from app.kernel.rag.internal_search_contracts import (
    CandidateRef,
    SearchProfile,
    sanitize_candidate_ref,
)
from app.kernel.rag.query_processing import clean_query, input_hash_for_query

# 锚点 fail-closed 丢弃原因码（与 E3 CANDIDATE_REF_DROPPED_NO_ANCHOR 同口径）。
STATUTE_REF_DROPPED_NO_ANCHOR = "STATUTE_REF_DROPPED_NO_ANCHOR"
# 互跳类案引用锚点不完整 / 不可溯源时的丢弃原因码。
STATUTE_CASE_REF_DROPPED_NO_ANCHOR = "STATUTE_CASE_REF_DROPPED_NO_ANCHOR"


# --- 数据层中间对象（纯结构化引用，绝不承载裁判正文 / 模型生成条文）----------------

@dataclass(frozen=True)
class StatuteHit:
    """法条索引/语料命中的最小结构化引用（数据层中间对象，非契约对象）。

    只承载指向 E5-2 法条语料的元数据与 ``text_id`` 锚点；``article_text`` 若有也只来自
    语料（catalog 模式为 None），服务层绝不生成/改写。最终经 ``sanitize_statute_ref``
    收敛为 StatuteRef 才对外暴露。
    """

    statute_id: str
    law_name: str
    text_id: str
    article_no: str | None = None
    article_text: str | None = None
    source_corpus: str | None = None
    effective_status: str | None = None


@dataclass(frozen=True)
class CaseLinkHit:
    """类案→法条 / 法条→类案 关联标注命中（数据层中间对象，源自 E5-2 关联映射）。

    只承载 case_id + 可选案件元数据 + 案件来源 chunk 锚点；绝不承载裁判正文。
    最终经 ``sanitize_candidate_ref`` 收敛为 CandidateRef 才对外暴露（互跳无对侧正文）。
    """

    case_id: str
    source_chunk_ids: tuple[str, ...] = ()
    case_number: str | None = None
    court: str | None = None
    trial_level: str | None = None
    case_cause: str | None = None
    judgment_date: str | None = None


@runtime_checkable
class StatuteCorpusPort(Protocol):
    """内核法条检索数据端口（依赖注入接口，便于测试用 fake 替换）。

    三个只读方法分别支撑三个服务入口；实现方只回结构化引用 + 锚点，绝不回正文。
    """

    def search_statutes(self, query_text: str, *, limit: int) -> list[StatuteHit]:
        """按查询文本召回法条命中（实现可走法条索引/词法匹配，本步不 gate 排序质量）。"""
        ...

    def statutes_for_case(self, case_id: str, *, limit: int) -> list[StatuteHit]:
        """按 case_id 取 E5-2 类案→法条关联标注命中的法条。"""
        ...

    def cases_for_statute(self, statute_id: str, *, limit: int) -> list[CaseLinkHit]:
        """按 statute_id 取 E5-2 关联标注命中的类案（带案件来源 chunk 锚点）。"""
        ...


# --- 默认数据端口实现：只读 E5-2 法条产物（懒加载 + 缓存，绝不写 / 绝不改案件产物）------

def _resolve_processed_dir(processed_dir: str | None = None) -> Path:
    """解析 E5-2 法条产物目录（默认仓库 data/processed；可经参数 / 环境变量覆盖）。"""
    if processed_dir and str(processed_dir).strip():
        return Path(str(processed_dir).strip())
    env = os.environ.get("STATUTE_CORPUS_DIR", "").strip()
    if env:
        return Path(env)
    # statute_search_service.py 位于 app/kernel/rag/ 下；仓库根在上 5 层。
    repo_root = Path(__file__).resolve().parents[5]
    return repo_root / "data" / "processed"


def _iter_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


class JsonlStatuteCorpus:
    """默认法条数据端口：读 E5-2 ``statutes.jsonl`` + ``case_statute_links.jsonl``。

    - 只读：绝不写 / 改任何案件或法条产物。
    - 懒加载 + 进程内缓存：首次访问才读文件，避免 import 期 I/O。
    - search_statutes 走**确定性词法匹配**（法名 / 条号 / text_id），本步不引入向量召回，
      不改任何既有召回/排序默认；命中质量非本步 gate 项（端点/评测在 E5-4 之后）。
    - 类案来源 chunk 锚点从 ``chunks.jsonl`` 取该 case 的代表性 chunk（只读，取 chunk_id）；
      取不到则该类案在互跳时被 fail-closed 丢弃（无锚点不暴露不可溯源候选）。
    """

    def __init__(self, processed_dir: str | None = None) -> None:
        self._dir = _resolve_processed_dir(processed_dir)
        self._statutes: dict[str, StatuteHit] | None = None
        self._case_to_statute_ids: dict[str, list[str]] | None = None
        self._statute_to_case_ids: dict[str, list[str]] | None = None
        self._case_chunk: dict[str, str] | None = None

    # --- 懒加载 ---------------------------------------------------------------

    def _ensure_statutes(self) -> dict[str, StatuteHit]:
        if self._statutes is None:
            out: dict[str, StatuteHit] = {}
            for row in _iter_jsonl(self._dir / "statutes.jsonl"):
                sid = str(row.get("statute_id") or "").strip()
                text_id = str(row.get("text_id") or "").strip()
                law_name = str(row.get("law_name") or "").strip()
                if not sid or not text_id or not law_name:
                    continue  # 缺 statute_id / text_id / law_name 不可用，跳过
                out[sid] = StatuteHit(
                    statute_id=sid,
                    law_name=law_name,
                    text_id=text_id,
                    article_no=_opt_str(row.get("article_no")),
                    article_text=_opt_str(row.get("article_text")),
                    source_corpus=_opt_str(row.get("source_corpus")),
                    effective_status=_opt_str(row.get("effective_status")),
                )
            self._statutes = out
        return self._statutes

    def _ensure_links(self) -> None:
        if self._case_to_statute_ids is not None and self._statute_to_case_ids is not None:
            return
        case_to: dict[str, list[str]] = {}
        statute_to: dict[str, list[str]] = {}
        for row in _iter_jsonl(self._dir / "case_statute_links.jsonl"):
            case_id = str(row.get("case_id") or "").strip()
            if not case_id:
                continue
            refs = row.get("statute_refs") or []
            for ref in refs:
                sid = str((ref or {}).get("statute_id") or "").strip()
                if not sid:
                    continue
                case_to.setdefault(case_id, [])
                if sid not in case_to[case_id]:
                    case_to[case_id].append(sid)
                statute_to.setdefault(sid, [])
                if case_id not in statute_to[sid]:
                    statute_to[sid].append(case_id)
        self._case_to_statute_ids = case_to
        self._statute_to_case_ids = statute_to

    def _ensure_case_chunk(self) -> dict[str, str]:
        """取每个 case 的一个代表性 source_chunk_id（只读 chunks.jsonl，绝不取正文）。"""
        if self._case_chunk is None:
            mapping: dict[str, str] = {}
            for row in _iter_jsonl(self._dir / "chunks.jsonl"):
                case_id = str(row.get("case_id") or "").strip()
                chunk_id = str(row.get("chunk_id") or "").strip()
                if case_id and chunk_id and case_id not in mapping:
                    mapping[case_id] = chunk_id
            self._case_chunk = mapping
        return self._case_chunk

    # --- 端口方法 -------------------------------------------------------------

    def search_statutes(self, query_text: str, *, limit: int) -> list[StatuteHit]:
        statutes = self._ensure_statutes()
        tokens = [t for t in (query_text or "").split() if t]
        scored: list[tuple[int, str, StatuteHit]] = []
        for sid, hit in statutes.items():
            haystack = " ".join(
                v for v in (hit.law_name, hit.article_no or "", hit.text_id) if v
            )
            if tokens:
                score = sum(1 for t in tokens if t in haystack)
            else:
                score = 0
            # 无 token 命中时仍按 statute_id 稳定序给出目录候选（确定性，不为提指标改排序）。
            scored.append((score, sid, hit))
        # 命中分降序、statute_id 升序：完全确定性，无随机、无 qrels/label。
        scored.sort(key=lambda x: (-x[0], x[1]))
        return [hit for _, _, hit in scored[: max(0, limit)]]

    def statutes_for_case(self, case_id: str, *, limit: int) -> list[StatuteHit]:
        self._ensure_links()
        statutes = self._ensure_statutes()
        assert self._case_to_statute_ids is not None
        sids = self._case_to_statute_ids.get(str(case_id).strip(), [])
        out: list[StatuteHit] = []
        for sid in sids[: max(0, limit)] if limit else sids:
            hit = statutes.get(sid)
            if hit is not None:
                out.append(hit)
        return out

    def cases_for_statute(self, statute_id: str, *, limit: int) -> list[CaseLinkHit]:
        self._ensure_links()
        case_chunk = self._ensure_case_chunk()
        assert self._statute_to_case_ids is not None
        case_ids = self._statute_to_case_ids.get(str(statute_id).strip(), [])
        out: list[CaseLinkHit] = []
        for case_id in (case_ids[: max(0, limit)] if limit else case_ids):
            chunk_id = case_chunk.get(case_id)
            anchors = (chunk_id,) if chunk_id else ()
            out.append(CaseLinkHit(case_id=case_id, source_chunk_ids=anchors))
        return out


def _opt_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


# --- 服务结果（只含契约对象 + 降级元信息，零正文）--------------------------------

@dataclass
class StatuteSearchResult:
    """法条检索结果（StatuteRef[] + 降级原因，绝不含裁判正文 / 模型生成条文）。"""

    statute_refs: list[StatuteRef] = field(default_factory=list)
    degraded: bool = False
    degraded_reasons: list[str] = field(default_factory=list)


@dataclass
class StatuteCaseRefResult:
    """法条→类案互跳结果（CandidateRef[] + 降级原因，零裁判正文）。"""

    candidate_refs: list[CandidateRef] = field(default_factory=list)
    degraded: bool = False
    degraded_reasons: list[str] = field(default_factory=list)


class StatuteSearchService:
    """内核法条检索服务：查询/类案 -> StatuteRef[]，法条 -> CandidateRef[]（互跳）。

    依赖经构造函数注入（默认 ``JsonlStatuteCorpus`` 读 E5-2 法条产物），便于测试用
    fake 替换；本服务不持久层句柄、不写库、不接端点、不依赖任何业务 flag on 路径。
    """

    def __init__(
        self,
        *,
        corpus: StatuteCorpusPort | None = None,
        default_limit: int = 10,
    ) -> None:
        self._corpus: StatuteCorpusPort = corpus or JsonlStatuteCorpus()
        self._default_limit = max(1, int(default_limit))

    # --- 入口 1：查询 / SearchProfile -> StatuteRef[] ---------------------------

    def search_statutes(
        self,
        query: SearchProfile | str,
        *,
        limit: int | None = None,
        query_session_id: str | None = None,
    ) -> StatuteSearchResult:
        """按查询文本召回法条，返回 StatuteRef[]（带 text_id 锚点，无裁判正文）。

        命中无锚点 / 不可收敛白名单 -> fail-closed 丢弃并记 ``STATUTE_REF_DROPPED_NO_ANCHOR``。
        日志只写 query_session_id / input_hash / 计数 / dropped，绝不写 query_text。
        """
        cap = self._cap(limit)
        query_text = clean_query(self._query_text(query) or "")
        input_hash = input_hash_for_query(query_text)
        hits = self._corpus.search_statutes(query_text, limit=cap)

        statute_refs, dropped = self._hits_to_statute_refs(hits)
        degraded_reasons = _repeat(STATUTE_REF_DROPPED_NO_ANCHOR, dropped)
        logger.info(
            "statute_search_completed query_session_id=%s input_hash=%s "
            "statute_count=%s dropped_no_anchor=%s",
            query_session_id or "",
            input_hash,
            len(statute_refs),
            dropped,
        )
        return StatuteSearchResult(
            statute_refs=statute_refs,
            degraded=bool(dropped),
            degraded_reasons=degraded_reasons,
        )

    # --- 入口 2：CandidateRef / case_id -> StatuteRef[]（类案→法条互跳）----------

    def statutes_by_case(
        self,
        case: CandidateRef | str,
        *,
        limit: int | None = None,
        query_session_id: str | None = None,
    ) -> StatuteSearchResult:
        """基于 E5-2 类案→法条关联标注，返回该类案关联的 StatuteRef[]（无裁判正文）。"""
        cap = self._cap(limit)
        case_id = self._case_id(case)
        hits = self._corpus.statutes_for_case(case_id, limit=cap) if case_id else []

        statute_refs, dropped = self._hits_to_statute_refs(hits)
        degraded_reasons = _repeat(STATUTE_REF_DROPPED_NO_ANCHOR, dropped)
        logger.info(
            "statute_by_case_completed query_session_id=%s case_present=%s "
            "statute_count=%s dropped_no_anchor=%s",
            query_session_id or "",
            bool(case_id),
            len(statute_refs),
            dropped,
        )
        return StatuteSearchResult(
            statute_refs=statute_refs,
            degraded=bool(dropped),
            degraded_reasons=degraded_reasons,
        )

    # --- 入口 3（可选已实现）：StatuteRef / statute_id -> CandidateRef[]（法条→类案）---

    def cases_by_statute(
        self,
        statute: StatuteRef | str,
        *,
        limit: int | None = None,
        query_session_id: str | None = None,
    ) -> StatuteCaseRefResult:
        """基于 E5-2 关联标注做「法条→类案」互跳，返回 CandidateRef[]。

        CandidateRef 经 ``sanitize_candidate_ref`` 严格收敛 E-1 白名单七字段 + 锚点校验；
        无 source_anchors / 不可溯源 -> fail-closed 丢弃并记 ``STATUTE_CASE_REF_DROPPED_NO_ANCHOR``。
        互跳不携带任何裁判正文 / summary / highlight。
        """
        cap = self._cap(limit)
        statute_id = self._statute_id(statute)
        hits = self._corpus.cases_for_statute(statute_id, limit=cap) if statute_id else []

        candidate_refs: list[CandidateRef] = []
        dropped = 0
        for hit in hits:
            ref = _safe_case_ref(hit)
            if ref is None:
                dropped += 1
                continue
            candidate_refs.append(ref)

        degraded_reasons = _repeat(STATUTE_CASE_REF_DROPPED_NO_ANCHOR, dropped)
        logger.info(
            "cases_by_statute_completed query_session_id=%s statute_present=%s "
            "candidate_count=%s dropped_no_anchor=%s",
            query_session_id or "",
            bool(statute_id),
            len(candidate_refs),
            dropped,
        )
        return StatuteCaseRefResult(
            candidate_refs=candidate_refs,
            degraded=bool(dropped),
            degraded_reasons=degraded_reasons,
        )

    # --- 内部纯转换 -----------------------------------------------------------

    def _hits_to_statute_refs(
        self, hits: Sequence[StatuteHit]
    ) -> tuple[list[StatuteRef], int]:
        refs: list[StatuteRef] = []
        dropped = 0
        for hit in hits:
            ref = _safe_statute_ref(hit)
            if ref is None:
                dropped += 1
                continue
            refs.append(ref)
        return refs, dropped

    def _cap(self, limit: int | None) -> int:
        if limit is None:
            return self._default_limit
        return max(1, int(limit))

    @staticmethod
    def _query_text(query: SearchProfile | str) -> str | None:
        if isinstance(query, SearchProfile):
            return query.query_text
        if isinstance(query, str):
            return query
        raise TypeError("search_statutes 仅接受 SearchProfile 或 str 查询文本")

    @staticmethod
    def _case_id(case: CandidateRef | str) -> str:
        if isinstance(case, CandidateRef):
            return str(case.case_id).strip()
        if isinstance(case, str):
            return case.strip()
        raise TypeError("statutes_by_case 仅接受 CandidateRef 或 case_id 字符串")

    @staticmethod
    def _statute_id(statute: StatuteRef | str) -> str:
        if isinstance(statute, StatuteRef):
            return str(statute.statute_id).strip()
        if isinstance(statute, str):
            return statute.strip()
        raise TypeError("cases_by_statute 仅接受 StatuteRef 或 statute_id 字符串")


# --- 模块级纯转换 / 辅助（无副作用，不含正文）------------------------------------

def _safe_statute_ref(hit: StatuteHit) -> StatuteRef | None:
    """把 StatuteHit 转为 StatuteRef；缺锚点 / 不可收敛 -> 丢弃（返回 None）。

    只搬运指向法条语料的元数据与 ``text_id`` 锚点；``article_text`` 原样透传（只来自语料，
    服务不生成）。一律经 ``sanitize_statute_ref`` 收敛白名单 + 锚点 fail-closed。
    """
    if not hit.text_id or not str(hit.text_id).strip():
        return None
    anchor: dict[str, Any] = {
        "text_id": hit.text_id,
        "law_name": hit.law_name,
        "article_no": hit.article_no,
        "anchor_type": "statute",
    }
    payload: dict[str, Any] = {
        "statute_id": hit.statute_id,
        "law_name": hit.law_name,
        "article_no": hit.article_no,
        "statute_anchors": [anchor],
        "article_text": hit.article_text,
        "source_corpus": hit.source_corpus,
        "effective_status": hit.effective_status,
    }
    payload = {k: v for k, v in payload.items() if v is not None}
    try:
        return sanitize_statute_ref(payload)
    except ContractViolationError:
        return None


def _safe_case_ref(hit: CaseLinkHit) -> CandidateRef | None:
    """把 CaseLinkHit 转为 CandidateRef（互跳）；锚点不完整 -> 丢弃（返回 None）。

    复用 E3 ``sanitize_candidate_ref`` 同款 E-1 白名单七字段 + 锚点校验；
    绝不搬运 summary / highlight / matched_text / 正文型字段（fail-closed）。
    """
    chunk_ids = [c for c in hit.source_chunk_ids if c and str(c).strip()]
    anchors = [
        {"case_id": hit.case_id, "source_chunk_id": chunk_id, "anchor_type": "statute_link"}
        for chunk_id in chunk_ids
    ]
    payload: dict[str, Any] = {
        "case_id": hit.case_id,
        "case_number": hit.case_number,
        "court": hit.court,
        "trial_level": hit.trial_level,
        "case_cause": hit.case_cause,
        "judgment_date": hit.judgment_date,
        "source_anchors": anchors,
    }
    payload = {k: v for k, v in payload.items() if v is not None}
    try:
        return sanitize_candidate_ref(payload)
    except ContractViolationError:
        return None


def _repeat(reason: str, count: int) -> list[str]:
    """生成 count 条同一降级原因码（dropped 计数语义，与 E3 drop_reasons 同形）。"""
    return [reason for _ in range(max(0, count))]


__all__ = [
    "StatuteSearchService",
    "StatuteSearchResult",
    "StatuteCaseRefResult",
    "StatuteCorpusPort",
    "JsonlStatuteCorpus",
    "StatuteHit",
    "CaseLinkHit",
    "STATUTE_REF_DROPPED_NO_ANCHOR",
    "STATUTE_CASE_REF_DROPPED_NO_ANCHOR",
]
