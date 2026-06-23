"""M5-7 只读数据质量统计。

只读 data/processed 下的元数据字段做聚合统计，输出供 gate.py 判定的统计字典。
绝不修改数据、不读取/回显裁判文书正文（不触碰 全文/当事人），
不落具名法官姓名（只判断字段存在性与完整度）。
"""
from __future__ import annotations

import collections
import re
from pathlib import Path
from typing import Any

from app.core.config import PROJECT_ROOT

CASES_PATH = PROJECT_ROOT / "data" / "processed" / "cases.jsonl"
# M5-7 路线B 扩充语料（裁判文书网备份的只读元数据，零正文/零当事人）。
TENDENCY_CORPUS_PATH = PROJECT_ROOT / "data" / "processed" / "tendency_corpus_meta.jsonl"

# 可能的结构化法官字段名（存在性探测；不读取其内容样本）。
_JUDGE_FIELD_CANDIDATES = (
    "judge", "judges", "presiding_judge", "judge_name",
    "审判长", "审判员", "合议庭",
)

_YEAR_RE = re.compile(r"(\d{4})")
# 业务合理裁判年份区间；越界视为异常（解析错误/脏数据）。
# 下限放宽到 1985（扩充语料合法跨度从 1985 起）；上限 2021（数据采集于 2023）。
_VALID_YEAR_MIN = 1985
_VALID_YEAR_MAX = 2021


def _norm(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return " ".join(str(v) for v in value).strip()
    return str(value).strip()


def _iter_cases(path: Path):
    if not path.is_file():
        raise FileNotFoundError(f"missing processed cases file: {path}")
    import json
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)


def _classify_domain(case: dict[str, Any]) -> str:
    """粗分案件领域（只用结构化字段，不读正文）。

    优先用扩充语料已带的显式 domain 字段；否则从 crime_type/case_cause 推断。
    """
    explicit = _norm(case.get("domain"))
    if explicit and explicit != "unknown":
        return explicit
    if _norm(case.get("crime_type")):
        return "criminal"
    cause = _norm(case.get("case_cause"))
    if any(k in cause for k in ("罪", "刑")):
        return "criminal"
    if any(k in cause for k in ("行政", "复议", "工伤认定")):
        return "administrative"
    if cause:
        return "civil"
    return "unknown"


def compute_tendency_metrics(path: Path | None = None) -> dict[str, Any]:
    cases = list(_iter_cases(path or CASES_PATH))
    n = len(cases)
    safe_n = max(n, 1)

    court_levels = collections.Counter(_norm(c.get("court_level")) or "(empty)" for c in cases)
    trial_levels = collections.Counter(_norm(c.get("trial_level")) or "(empty)" for c in cases)
    per_court = collections.Counter(_norm(c.get("court")) for c in cases)
    domains = collections.Counter(_classify_domain(c) for c in cases)
    causes = collections.Counter(_norm(c.get("case_cause")) or "(empty)" for c in cases)

    # 法官字段存在性 / 完整度（只判断键是否存在且非空，不取姓名）。
    judge_present = False
    judge_filled = 0
    for c in cases:
        present_keys = [k for k in c.keys() if k in _JUDGE_FIELD_CANDIDATES]
        if present_keys:
            judge_present = True
            if any(_norm(c.get(k)) for k in present_keys):
                judge_filled += 1
    judge_completeness = (judge_filled / safe_n) if judge_present else 0.0

    # 统计单元（法院）达标数：样本量 ≥30 的法院个数。
    adequate_units = [court for court, cnt in per_court.items() if court and cnt >= 30]
    units_adequate = len(adequate_units)
    min_per_adequate = min((per_court[c] for c in adequate_units), default=0)

    # 数据来源可声明度：source_url / source_name / source_updated_at 三项，按比例等权。
    src_url_filled = sum(1 for c in cases if _norm(c.get("source_url")))
    src_name_filled = sum(1 for c in cases if _norm(c.get("source_name")))
    src_updated_filled = sum(1 for c in cases if _norm(c.get("source_updated_at")))
    src_updated_present = any("source_updated_at" in c for c in cases)
    transparency = (
        (src_url_filled / safe_n) * (1 / 3)
        + (src_name_filled / safe_n) * (1 / 3)
        + (src_updated_filled / safe_n) * (1 / 3)
    )

    # 地域缺失率。
    region_missing = sum(1 for c in cases if not _norm(c.get("region")))
    region_missing_rate = region_missing / safe_n

    # 时间跨度 / 日期异常率。新语料用 judgment_year；旧语料用 judgment_date。
    years: list[int] = []
    anomaly = 0
    for c in cases:
        raw_year = _norm(c.get("judgment_year")) or _norm(c.get("judgment_date"))
        m = _YEAR_RE.match(raw_year)
        if not m:
            anomaly += 1
            continue
        y = int(m.group(1))
        years.append(y)
        if y < _VALID_YEAR_MIN or y > _VALID_YEAR_MAX:
            anomaly += 1
    date_anomaly_rate = anomaly / safe_n
    year_min = min(years) if years else None
    year_max = max(years) if years else None

    top_cause = causes.most_common(1)[0][1] if causes else 0
    top_cause_share = top_cause / safe_n

    return {
        "total_cases": n,
        "court_level_counts": dict(court_levels),
        "trial_level_counts": dict(trial_levels),
        "distinct_courts": sum(1 for k in per_court if k),
        "units_with_adequate_sample": units_adequate,
        "min_sample_per_adequate_unit": min_per_adequate,
        "per_unit_note": (
            f"{sum(1 for k in per_court if k)} ge fayuan, yangben>=30 de you {units_adequate} ge; "
            f"zhongwei yangben/fayuan={_median(list(per_court.values()))}"
        ),
        "judge_field_present": judge_present,
        "judge_field_completeness": judge_completeness,
        "case_type_domains": [d for d, cnt in domains.items() if d != "unknown" and cnt > 0],
        "source_transparency": transparency,
        "source_fields": {
            "source_url_filled": src_url_filled,
            "source_name_filled": src_name_filled,
            "source_updated_at_filled": src_updated_filled,
            "source_updated_at_present": src_updated_present,
        },
        "source_note": (
            f"source_url filled {src_url_filled}/{n}; source_name filled {src_name_filled}/{n}; "
            f"source_updated_at filled {src_updated_filled}/{n}"
        ),
        "region_missing_rate": region_missing_rate,
        "distinct_case_cause": sum(1 for k in causes if k != "(empty)"),
        "top_case_cause_share": top_cause_share,
        "date_anomaly_rate": date_anomaly_rate,
        "year_min": year_min,
        "year_max": year_max,
        "date_note": (
            f"parseable year range [{year_min}, {year_max}]; "
            f"anomaly(out-of-range/no-year) {anomaly}"
        ),
    }


def _median(values: list[int]) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    mid = len(s) // 2
    if len(s) % 2:
        return float(s[mid])
    return (s[mid - 1] + s[mid]) / 2
