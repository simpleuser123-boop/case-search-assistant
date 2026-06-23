# -*- coding: utf-8 -*-
"""E5-2 类案→法条关联标注管道（复用既有 law_articles -> case_statute_links.jsonl）。

设计依据：落地设计文档/20-E5法条检索分步骤系统提示词文档.md §5（E5-2 目标 3）。

口径：
- **只读**既有案件产物 cases.jsonl（law_articles 源自 JuDGE「Law Articles」），
  **绝不修改/重建** cases.jsonl / chunks.jsonl / 案件 Chroma 索引。
- 建立 case_id -> 法条 text_id（+ statute_id）的关联映射，作为 E5-3 法条↔类案
  互跳的**离线数据底座**；映射用 build_statute_corpus 同口径的 derive_statute_id /
  derive_catalog_text_id，确保与 statutes.jsonl / statute_chunks.jsonl 可 join。
- qrels（案件→法条相关性标注）**不进本映射、不进运行时**；本映射仅是「案件实际引用了
  哪些法条」的事实性关联（来自判决书 Law Articles），非相关性标签。
- 产物只含 case_id / statute_id / text_id / law_name / article_no 等结构化字段，
  **不含裁判正文 / PII / chunk 正文**。

不接线任何端点/前端/内核服务；产物供 E5-3 内核法条检索服务离线消费。
"""
from __future__ import annotations

import argparse
import datetime
import json
from pathlib import Path
from typing import Any

try:  # 包内导入（host `-m app.kernel.data.pipeline...` 权威路径）
    from app.kernel.data.pipeline.build_statute_corpus import (
        COVERAGE_DOMAIN,
        DEFAULT_LAW_NAME,
        derive_catalog_text_id,
        derive_statute_id,
        _normalize_article_no,
    )
except ModuleNotFoundError:  # 直接按文件执行（VM smoke，无 app 包路径）：按文件加载同目录纯函数
    import importlib.util as _ilu

    _sib = Path(__file__).with_name("build_statute_corpus.py")
    _spec = _ilu.spec_from_file_location("_e5_build_statute_corpus", _sib)
    _mod = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)  # type: ignore[union-attr]
    COVERAGE_DOMAIN = _mod.COVERAGE_DOMAIN
    DEFAULT_LAW_NAME = _mod.DEFAULT_LAW_NAME
    derive_catalog_text_id = _mod.derive_catalog_text_id
    derive_statute_id = _mod.derive_statute_id
    _normalize_article_no = _mod._normalize_article_no


def build_links_for_case(
    case_id: str,
    law_articles: list[int | str],
    *,
    law_name: str = DEFAULT_LAW_NAME,
) -> dict[str, Any] | None:
    """一条案件 + 其 law_articles -> 一条关联记录（去重、稳定排序）。

    无 law_articles 返回 None（不产空关联）。每个法条引用带 statute_id + text_id 锚点，
    与 statutes.jsonl 同口径，便于 E5-3 join。
    """
    refs: dict[str, dict[str, str]] = {}
    for raw in law_articles or []:
        art = _normalize_article_no(raw)
        if not art:
            continue
        sid = derive_statute_id(law_name, art)
        if sid in refs:
            continue
        refs[sid] = {
            "statute_id": sid,
            "law_name": law_name,
            "article_no": art,
            "text_id": derive_catalog_text_id(law_name, art),
        }
    if not refs:
        return None

    def _sort_key(r: dict[str, str]):
        a = r["article_no"]
        return (0, int(a)) if a.isdigit() else (1, a)

    return {
        "case_id": case_id,
        "coverage_domain": COVERAGE_DOMAIN,
        "statute_refs": [refs[k] for k in sorted(refs, key=lambda k: _sort_key(refs[k]))],
    }


def run(
    *,
    cases_path: str = "data/processed/cases.jsonl",
    out_dir: str = "data/processed",
    links_name: str = "case_statute_links.jsonl",
    law_name: str = DEFAULT_LAW_NAME,
    dry_run: bool = False,
    sample: int = 0,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """只读 cases.jsonl -> case_statute_links.jsonl（幂等，从不写案件产物）。"""
    cp = Path(cases_path)
    if not cp.is_file():
        raise SystemExit(f"案件产物不存在：{cases_path}（关联标注需只读 cases.jsonl）")

    n_cases = 0
    n_linked = 0
    total_refs = 0
    distinct_statutes: set[str] = set()
    links: list[dict[str, Any]] = []

    with open(cp, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            n_cases += 1
            link = build_links_for_case(
                rec.get("case_id", ""), rec.get("law_articles") or [], law_name=law_name
            )
            if link is None:
                continue
            n_linked += 1
            total_refs += len(link["statute_refs"])
            for r in link["statute_refs"]:
                distinct_statutes.add(r["statute_id"])
            links.append(link)

    report = {
        "step": "E5-2-build_case_statute_links",
        "generated_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": "data/processed/cases.jsonl(law_articles, read-only)",
        "coverage_domain": COVERAGE_DOMAIN,
        "cases_seen": n_cases,
        "cases_with_links": n_linked,
        "case_link_coverage_pct": round(n_linked / n_cases * 100, 2) if n_cases else 0.0,
        "total_statute_refs": total_refs,
        "distinct_statutes_referenced": len(distinct_statutes),
        "avg_statutes_per_linked_case": round(total_refs / n_linked, 2) if n_linked else 0.0,
        "qrels_used": False,
        "reads_case_products_only": True,
        "modifies_case_products": False,
        "dry_run": dry_run,
        "output": str(Path(out_dir) / links_name),
    }

    if not dry_run:
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        with open(out / links_name, "w", encoding="utf-8") as f:
            for link in links:
                f.write(json.dumps(link, ensure_ascii=False) + "\n")

    samples: list[dict[str, Any]] = []
    if sample:
        for link in links[:sample]:
            samples.append({
                "case_id": link["case_id"],
                "n_statute_refs": len(link["statute_refs"]),
                "statute_refs": link["statute_refs"][:5],
            })
    return report, samples


def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="build_case_statute_links",
        description="E5-2 case->statute links: read-only cases.jsonl law_articles -> case_statute_links.jsonl (no case-product mutation, no qrels)",
    )
    ap.add_argument("--cases", default="data/processed/cases.jsonl", help="case product (read-only input)")
    ap.add_argument("--out", default="data/processed", help="links output dir")
    ap.add_argument("--links-name", default="case_statute_links.jsonl")
    ap.add_argument("--law-name", default=DEFAULT_LAW_NAME)
    ap.add_argument("--dry-run", action="store_true", help="do not write; print stats + samples only")
    ap.add_argument("--sample", type=int, default=0)
    return ap


if __name__ == "__main__":
    args = build_arg_parser().parse_args()
    rep, samp = run(
        cases_path=args.cases,
        out_dir=args.out,
        links_name=args.links_name,
        law_name=args.law_name,
        dry_run=args.dry_run,
        sample=args.sample,
    )
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    if samp:
        print("\n=== SAMPLES ===")
        print(json.dumps(samp, ensure_ascii=False, indent=2))
