# -*- coding: utf-8 -*-
"""E5-2 法条语料规整管道（法条语料种子 -> statutes.jsonl / statute_chunks.jsonl）。

设计依据：落地设计文档/20-E5法条检索分步骤系统提示词文档.md §1、§5（E5-2）。
对标既有 parse_judge.py（4.3 案件解析）的纯离线、幂等、日志不落正文范式，
但**产物与案件产物物理隔离**（独立文件名 statutes.jsonl / statute_chunks.jsonl，
不碰 cases.jsonl / chunks.jsonl / tendency_corpus_meta.jsonl）。

第一性约束（E5-1 红线，本模块严格遵守）：
- 法条条文**只来自法条语料种子、原样保留**（不改写/不补全/不由模型生成）。
- 每条法条 chunk **必带 text_id 锚点**；缺锚点的记录被丢弃且计数，绝不进可展示集。
- 不引入对外业务 flag；构建开关只用脚本参数。
- 日志/报告/产物只回统计与结构化字段，不打印条文长正文（report 仅截短抽样）。

两种输入模式（同一 statute_id 派生口径，可平滑合流）：
- ``seed``  ：规整真实法条语料种子（JuDGE law_corpus.jsonl：{text_id, text, name}），
              article_text 原样来自 text 字段，text_id 直接作为锚点。
- ``catalog``（默认，无种子时可立即运行）：从既有 **cases.jsonl 的 law_articles**
              派生「法条目录」——只产出 law_name/article_no/text_id 锚点与结构化元数据，
              **article_text 留空（null）**，绝不杜撰条文。catalog 与 seed 的 statute_id
              口径一致，待种子到位后 seed 模式可补齐 article_text 而不破坏关联。

本模块不接线任何端点/前端/内核服务，产物供 E5-3 内核法条检索服务离线消费。
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable

# --- 口径常量（与 statute_contract.STATUTE_REF_FIELDS / 文档 16 §4.1.1 对齐）-------

# 当前法条语料覆盖范围：刑事（民事/行政为已知缺口，见 README/报告 coverage 标注）。
COVERAGE_DOMAIN = "criminal"
# 中国刑法法名（catalog 模式下 law_articles 均为《刑法》条号）。
DEFAULT_LAW_NAME = "中华人民共和国刑法"
# 语料来源标识（短，非正文；与文档示例 judge_law_corpus 同口径）。
SOURCE_CORPUS_CATALOG = "case_law_articles_catalog"
SOURCE_CORPUS_SEED = "judge_law_corpus"
# text_id 锚点命名空间（catalog 自铸锚点，seed 用种子自带 text_id）。
CATALOG_TEXT_ID_NS = "cncl"  # 中华人民共和国刑法 catalog 锚点前缀

# 法名 -> ASCII slug（用于稳定 statute_id，避免非 ASCII 路径/键问题）。
LAW_NAME_SLUG = {
    DEFAULT_LAW_NAME: "cn_criminal_law",
    "刑法": "cn_criminal_law",
}


def _law_slug(law_name: str) -> str:
    """法名 -> 稳定 ASCII slug；未登记法名用 sha1 短哈希兜底（仍稳定、可复现）。"""
    if law_name in LAW_NAME_SLUG:
        return LAW_NAME_SLUG[law_name]
    h = hashlib.sha1(law_name.encode("utf-8")).hexdigest()[:8]
    return f"law_{h}"


def derive_statute_id(law_name: str, article_no: str | int) -> str:
    """(法名, 条号) -> 稳定 statute_id（seed 与 catalog 共用，保证可合流）。

    例：("中华人民共和国刑法", 133) -> "cn_criminal_law_art_133"。
    article_no 归一为去空白字符串，便于「第133条」「133」同一化。
    """
    art = _normalize_article_no(article_no)
    return f"{_law_slug(law_name)}_art_{art}"


def derive_catalog_text_id(law_name: str, article_no: str | int) -> str:
    """catalog 模式自铸 text_id 锚点（指向法条目录条目，区别案件锚点）。

    例："cncl::cn_criminal_law::art_133"。命名空间前缀显式标注 catalog 来源，
    待真实种子到位后由 seed 模式的 text_id 取代为权威语料锚点。
    """
    art = _normalize_article_no(article_no)
    return f"{CATALOG_TEXT_ID_NS}::{_law_slug(law_name)}::art_{art}"


def _normalize_article_no(article_no: str | int) -> str:
    """条号归一：'第133条' / '133' / 133 -> '133'；非数字条号保留清洗后的串。"""
    s = str(article_no).strip()
    m = re.search(r"\d+", s)
    return m.group(0) if m else re.sub(r"\s+", "", s)


# --- 法条记录 / chunk 规整（纯函数，便于单测）-----------------------------------

def normalize_seed_record(rec: dict[str, Any]) -> dict[str, Any] | None:
    """规整一条**真实种子**法条记录 -> 标准法条记录（article_text 原样来自语料）。

    种子约定字段（JuDGE law_corpus.jsonl）：text_id / text / name(法名) / [article_no]。
    红线：
    - text 原样保留作为 article_text，不改写/不补全/不生成。
    - text_id 缺失即返回 None（缺锚点不进可展示集，由调用方计数丢弃）。
    """
    text_id = rec.get("text_id")
    if not text_id or not str(text_id).strip():
        return None  # 缺锚点：丢弃
    text_id = str(text_id).strip()
    law_name = (rec.get("name") or rec.get("law_name") or DEFAULT_LAW_NAME).strip()
    raw_article = rec.get("article_no") or rec.get("article") or _infer_article_from_text(rec.get("text", ""))
    article_no = _normalize_article_no(raw_article) if raw_article else ""
    article_text = rec.get("text")
    article_text = article_text if (article_text and str(article_text).strip()) else None
    statute_id = (
        derive_statute_id(law_name, article_no) if article_no else f"{_law_slug(law_name)}_{text_id}"
    )
    return {
        "statute_id": statute_id,
        "law_name": law_name,
        "article_no": article_no,
        "text_id": text_id,
        "article_text": article_text,  # 原样来自语料，不改写
        "source_corpus": SOURCE_CORPUS_SEED,
        "effective_status": "current",
        "coverage_domain": COVERAGE_DOMAIN,
    }


def _infer_article_from_text(text: str) -> str:
    """从条文文本开头解析「第X条」条号（仅取数字，不改写正文，仅用于补 article_no）。"""
    if not text:
        return ""
    m = re.match(r"\s*第\s*([零〇一二三四五六七八九十百千0-9]+)\s*条", str(text))
    return m.group(1) if m else ""


def build_statute_chunk(statute: dict[str, Any]) -> dict[str, Any] | None:
    """法条记录 -> 法条 chunk（必带 text_id 锚点；缺锚点返回 None）。

    - seed 模式：chunk_type='statute_article'，text=原样条文（来自语料）。
    - catalog 模式（article_text 为空）：chunk_type='statute_label'，
      text 仅为「法名 + 第X条」结构化标签（事实性标识，非杜撰条文），仍带 text_id 锚点，
      使法条目录可被检索定位；待种子到位后由 seed chunk 取代承载真正条文。
    """
    text_id = statute.get("text_id")
    if not text_id or not str(text_id).strip():
        return None
    article_text = statute.get("article_text")
    if article_text and str(article_text).strip():
        chunk_type = "statute_article"
        text = str(article_text)  # 原样条文，不改写
        has_article_text = True
    else:
        chunk_type = "statute_label"
        art = statute.get("article_no") or ""
        label_art = f"第{art}条" if art else ""
        text = f"{statute.get('law_name', '')}{label_art}".strip()
        has_article_text = False
    return {
        "statute_chunk_id": f"{statute['statute_id']}::{text_id}",
        "statute_id": statute["statute_id"],
        "text_id": text_id,  # 锚点：100% 必带
        "law_name": statute.get("law_name", ""),
        "article_no": statute.get("article_no", ""),
        "chunk_type": chunk_type,
        "text": text,
        "has_article_text": has_article_text,
        "source_corpus": statute.get("source_corpus", ""),
        "coverage_domain": statute.get("coverage_domain", COVERAGE_DOMAIN),
    }


def build_catalog_statutes(law_articles: Iterable[int | str], law_name: str = DEFAULT_LAW_NAME) -> list[dict[str, Any]]:
    """从一组 law_articles 条号派生「法条目录」记录（catalog 模式，无 article_text）。

    去重、按数字条号排序；每条自铸 text_id 锚点（catalog 命名空间），article_text=None。
    红线：绝不杜撰条文，只产出可核验的法名 + 条号 + 锚点。
    """
    seen: dict[str, dict[str, Any]] = {}
    for raw in law_articles:
        art = _normalize_article_no(raw)
        if not art:
            continue
        sid = derive_statute_id(law_name, art)
        if sid in seen:
            continue
        seen[sid] = {
            "statute_id": sid,
            "law_name": law_name,
            "article_no": art,
            "text_id": derive_catalog_text_id(law_name, art),
            "article_text": None,  # catalog：无语料正文，绝不杜撰
            "source_corpus": SOURCE_CORPUS_CATALOG,
            "effective_status": "unverified",  # 目录条目，未经语料校验
            "coverage_domain": COVERAGE_DOMAIN,
        }

    def _sort_key(s: dict[str, Any]):
        a = s["article_no"]
        return (0, int(a)) if a.isdigit() else (1, a)

    return [seen[k] for k in sorted(seen, key=lambda k: _sort_key(seen[k]))]


def _iter_seed(seed_path: Path) -> Iterable[dict[str, Any]]:
    """读取种子语料（支持 jsonl 逐行 或 json 数组）。"""
    raw = seed_path.read_text(encoding="utf-8").strip()
    if not raw:
        return
    if raw[0] == "[":
        for rec in json.loads(raw):
            yield rec
    else:
        for line in raw.splitlines():
            line = line.strip()
            if line:
                yield json.loads(line)


def _load_case_law_articles(cases_path: Path) -> list[int | str]:
    """**只读** cases.jsonl，收集全部 law_articles（catalog 模式输入；不修改案件产物）。"""
    arts: list[int | str] = []
    with open(cases_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            for a in rec.get("law_articles") or []:
                arts.append(a)
    return arts


# --- 主流程（幂等：只覆盖法条产物，从不触碰案件产物）-----------------------------

def run(
    *,
    mode: str = "catalog",
    seed_path: str | None = None,
    cases_path: str = "data/processed/cases.jsonl",
    out_dir: str = "data/processed",
    statutes_name: str = "statutes.jsonl",
    chunks_name: str = "statute_chunks.jsonl",
    law_name: str = DEFAULT_LAW_NAME,
    dry_run: bool = False,
    sample: int = 0,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """规整法条语料 -> statutes.jsonl + statute_chunks.jsonl，返回 (report, samples)。

    mode='seed'    ：从 seed_path 规整真实语料（article_text 原样）。
    mode='catalog' ：从 cases.jsonl 的 law_articles 派生法条目录（article_text=None）。
    dry_run=True   ：不落盘，仅返回统计与抽样（用于 CI/无种子环境校验逻辑）。
    """
    if mode not in {"seed", "catalog"}:
        raise SystemExit(f"未知 mode={mode!r}，仅支持 'seed' | 'catalog'")

    dropped_no_anchor = 0
    statutes: list[dict[str, Any]] = []

    if mode == "seed":
        if not seed_path:
            raise SystemExit("mode='seed' 需要 --seed 指向法条语料种子（如 JuDGE law_corpus.jsonl）")
        sp = Path(seed_path)
        if not sp.is_file():
            raise SystemExit(f"种子语料不存在：{seed_path}（请放置 JuDGE law_corpus.jsonl 后重跑）")
        for rec in _iter_seed(sp):
            norm = normalize_seed_record(rec)
            if norm is None:
                dropped_no_anchor += 1
                continue
            statutes.append(norm)
    else:  # catalog
        cp = Path(cases_path)
        if not cp.is_file():
            raise SystemExit(f"案件产物不存在：{cases_path}（catalog 模式需只读 cases.jsonl）")
        statutes = build_catalog_statutes(_load_case_law_articles(cp), law_name=law_name)

    # 去重（按 statute_id），构建 chunk，强制锚点完整。
    uniq: dict[str, dict[str, Any]] = {}
    for s in statutes:
        uniq.setdefault(s["statute_id"], s)
    statutes = list(uniq.values())

    chunks: list[dict[str, Any]] = []
    chunk_missing_anchor = 0
    for s in statutes:
        ck = build_statute_chunk(s)
        if ck is None:
            chunk_missing_anchor += 1
            continue
        chunks.append(ck)

    n_statutes = len(statutes)
    n_with_text = sum(1 for s in statutes if s.get("article_text"))
    n_chunks = len(chunks)
    anchor_complete = all(c.get("text_id") for c in chunks)

    report = {
        "step": "E5-2-build_statute_corpus",
        "mode": mode,
        "generated_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "source_corpus": SOURCE_CORPUS_SEED if mode == "seed" else SOURCE_CORPUS_CATALOG,
        "coverage_domain": COVERAGE_DOMAIN,
        "coverage_note": "当前仅覆盖刑事法条；民事/行政为已知缺口，待 M5-7 路线 B 合并扩语料。",
        "statutes_count": n_statutes,
        "statute_chunks_count": n_chunks,
        "statutes_with_article_text": n_with_text,
        "article_text_present_rate_pct": round(n_with_text / n_statutes * 100, 2) if n_statutes else 0.0,
        "anchor_completeness_rate_pct": round(n_chunks / max(1, n_chunks) * 100, 2) if anchor_complete else 0.0,
        "all_chunks_have_text_id": anchor_complete,
        "dropped_seed_records_no_anchor": dropped_no_anchor,
        "chunks_dropped_missing_anchor": chunk_missing_anchor,
        "model_generated_article_text": 0,  # 红线：从不由模型生成条文
        "dry_run": dry_run,
        "outputs": {
            "statutes": str(Path(out_dir) / statutes_name),
            "statute_chunks": str(Path(out_dir) / chunks_name),
        },
    }

    if not dry_run:
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        with open(out / statutes_name, "w", encoding="utf-8") as f:
            for s in statutes:
                f.write(json.dumps(s, ensure_ascii=False) + "\n")
        with open(out / chunks_name, "w", encoding="utf-8") as f:
            for c in chunks:
                f.write(json.dumps(c, ensure_ascii=False) + "\n")

    samples: list[dict[str, Any]] = []
    if sample:
        for c in chunks[:sample]:
            samples.append({
                "statute_id": c["statute_id"],
                "text_id": c["text_id"],
                "law_name": c["law_name"],
                "article_no": c["article_no"],
                "chunk_type": c["chunk_type"],
                "has_article_text": c["has_article_text"],
                "text_snippet": (c["text"] or "")[:60],  # 截短，避免长正文进报告
            })
    return report, samples


def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="build_statute_corpus",
        description="E5-2 法条语料规整：法条语料种子/案件 law_articles -> statutes.jsonl + statute_chunks.jsonl（与案件产物物理隔离，不杜撰条文）",
    )
    ap.add_argument("--mode", choices=["seed", "catalog"], default="catalog",
                    help="seed=规整真实法条语料种子；catalog=从 cases.jsonl 的 law_articles 派生法条目录（默认）")
    ap.add_argument("--seed", default=None, help="法条语料种子路径（mode=seed 必填，如 data/raw/law_corpus.jsonl）")
    ap.add_argument("--cases", default="data/processed/cases.jsonl", help="案件产物（catalog 模式只读输入）")
    ap.add_argument("--out", default="data/processed", help="法条产物输出目录")
    ap.add_argument("--statutes-name", default="statutes.jsonl")
    ap.add_argument("--chunks-name", default="statute_chunks.jsonl")
    ap.add_argument("--law-name", default=DEFAULT_LAW_NAME, help="catalog 模式默认法名（law_articles 所属法）")
    ap.add_argument("--dry-run", action="store_true", help="不落盘，仅打印统计与抽样")
    ap.add_argument("--sample", type=int, default=0, help="打印前 N 条 chunk 抽样（截短，不落正文）")
    return ap


if __name__ == "__main__":
    args = build_arg_parser().parse_args()
    rep, samp = run(
        mode=args.mode,
        seed_path=args.seed,
        cases_path=args.cases,
        out_dir=args.out,
        statutes_name=args.statutes_name,
        chunks_name=args.chunks_name,
        law_name=args.law_name,
        dry_run=args.dry_run,
        sample=args.sample,
    )
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    if samp:
        print("\n=== SAMPLES (truncated) ===")
        print(json.dumps(samp, ensure_ascii=False, indent=2))
