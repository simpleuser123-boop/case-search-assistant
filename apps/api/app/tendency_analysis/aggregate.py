"""M5-8 F19 只读聚合统计。

只读 data/processed 下的只读元数据语料（与 M5-7 门禁同源），对
法院层级 / 审级 / 案件领域 / 案由 做分布聚合，输出供 service.py 组装展示结构。
绝不修改数据、不读取/回显裁判文书正文（不触碰 全文/当事人），
不落具名法官姓名，不做任何个案预测。
"""
from __future__ import annotations

import collections
from pathlib import Path
from typing import Any, Callable

from app.tendency_gate.metrics import (
    CASES_PATH,
    TENDENCY_CORPUS_PATH,
    _classify_domain,
    _iter_cases,
    _norm,
)

# 单个分组（bucket）样本量达到此门槛才视为"可解释"，否则标"样本不足"。
# 与 M5-7 门禁 MIN_SAMPLE_PER_UNIT=30 一致。
MIN_SAMPLE_PER_BUCKET = 30
# 每个 bucket 最多回传的 case_id 引用数（仅引用，非正文；防止响应膨胀）。
MAX_CASE_REFS_PER_BUCKET = 20
_EMPTY = {"(empty)", "(空)", ""}


def resolve_corpus_path() -> Path:
    """F19 针对专建只读元数据语料；存在则用它，否则回落 JuDGE（与门禁一致）。"""
    return TENDENCY_CORPUS_PATH if TENDENCY_CORPUS_PATH.is_file() else CASES_PATH


def _bucket_key_funcs() -> dict[str, Callable[[dict[str, Any]], str]]:
    """各聚合维度的分组键函数（只用结构化字段，不读正文）。"""
    return {
        "court_level": lambda c: _norm(c.get("court_level")) or "(empty)",
        "trial_level": lambda c: _norm(c.get("trial_level")) or "(empty)",
        "case_domain": _classify_domain,
        "case_cause": lambda c: _norm(c.get("case_cause")) or "(empty)",
    }


def aggregate_tendency(path: Path | None = None) -> dict[str, Any]:
    """对语料做只读聚合。返回 dict：维度 -> {total, buckets:[{label,sample_size,case_id_refs,case_id_total}]}。

    case_id 仅作可追溯引用收集（截断到 MAX_CASE_REFS_PER_BUCKET），不收集任何正文字段。
    """
    src = path or resolve_corpus_path()
    key_funcs = _bucket_key_funcs()

    counters: dict[str, collections.Counter] = {
        dim: collections.Counter() for dim in key_funcs
    }
    # 仅收集 case_id 引用（截断）；不存任何其它字段。
    refs: dict[str, dict[str, list[str]]] = {dim: {} for dim in key_funcs}
    totals: dict[str, int] = {dim: 0 for dim in key_funcs}

    n = 0
    for case in _iter_cases(src):
        n += 1
        case_id = _norm(case.get("case_id"))
        for dim, fn in key_funcs.items():
            label = fn(case)
            if label in _EMPTY or label == "unknown":
                continue
            counters[dim][label] += 1
            totals[dim] += 1
            if case_id:
                bucket_refs = refs[dim].setdefault(label, [])
                if len(bucket_refs) < MAX_CASE_REFS_PER_BUCKET:
                    bucket_refs.append(case_id)

    out: dict[str, Any] = {"total_cases": n, "dimensions": {}}
    for dim in key_funcs:
        total = totals[dim]
        buckets = []
        for label, cnt in counters[dim].most_common():
            buckets.append({
                "label": label,
                "sample_size": cnt,
                "share": (cnt / total) if total else 0.0,
                "sample_sufficient": cnt >= MIN_SAMPLE_PER_BUCKET,
                "case_id_refs": list(refs[dim].get(label, [])),
                "case_id_total": cnt,
            })
        out["dimensions"][dim] = {"total": total, "buckets": buckets}
    return out
