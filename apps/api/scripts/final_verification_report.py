"""Build the R6 final verification report from a release-gate run."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

SCRIPT_PATH = Path(__file__).resolve()
API_ROOT = SCRIPT_PATH.parents[1]
PROJECT_ROOT = SCRIPT_PATH.parents[3]
DOCS_DIR = PROJECT_ROOT / "docs" / "development"
REPORT_PREFIX = "m1.1-final-verification"

if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from scripts.release_gate import (  # noqa: E402
    collect_forbidden_terms,
    privacy_scan_paths,
    relative,
    scan_inline_and_files,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate the M1.1 R6 final verification report.")
    parser.add_argument("--gate-report", required=True, help="Path to the release gate JSON report.")
    parser.add_argument("--timestamp", default="", help="Optional output timestamp.")
    args = parser.parse_args()

    gate_report_path = Path(args.gate_report).resolve()
    if not gate_report_path.is_file():
        raise SystemExit(f"gate report not found: {gate_report_path}")

    timestamp = args.timestamp or datetime.now().strftime("%Y%m%d-%H%M%S")
    final_json_path = DOCS_DIR / f"{REPORT_PREFIX}-{timestamp}.json"
    final_md_path = DOCS_DIR / f"{REPORT_PREFIX}-{timestamp}.md"

    gate_report = json.loads(gate_report_path.read_text(encoding="utf-8"))
    if not isinstance(gate_report, dict):
        raise SystemExit("gate report is not a JSON object")

    checks = build_checks(gate_report)
    conclusions = build_conclusions(gate_report, checks)
    path_check = build_path_check(checks)

    report = {
        "version": "m1_1_r6_final_verification_v1",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "project_root": str(PROJECT_ROOT),
        "source_gate_report": str(gate_report_path),
        "source_gate_markdown": str(gate_report.get("reports", {}).get("markdown", "")),
        "policy": {
            "default_feature_flags_must_remain_closed": True,
            "does_not_modify_business_logic": True,
            "does_not_rebuild_vector_indexes": True,
            "does_not_clear_chroma": True,
            "does_not_write_raw_query_or_case_text": True,
        },
        "checks": checks,
        "conclusions": conclusions,
        "path_check": path_check,
        "reports": {
            "json": str(final_json_path),
            "markdown": str(final_md_path),
        },
    }

    markdown = render_markdown(report)
    privacy = build_privacy_check(report, markdown, gate_report, final_json_path, final_md_path)
    report["privacy_check"] = privacy
    markdown = render_markdown(report)

    final_json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    final_md_path.write_text(markdown + "\n", encoding="utf-8")

    print(json.dumps(
        {
            "status": conclusions["overall_status"],
            "json_report": str(final_json_path),
            "markdown_report": str(final_md_path),
            "internal_basic_search": conclusions["internal_basic_search"]["decision"],
            "external_gray": conclusions["external_gray"]["decision"],
            "new_rerank": conclusions["new_rerank"]["decision"],
        },
        ensure_ascii=False,
        indent=2,
    ))
    return 0


def build_checks(gate_report: dict[str, Any]) -> list[dict[str, Any]]:
    gates = gate_report.get("gates") or []
    checks: list[dict[str, Any]] = []
    for gate in gates:
        if not isinstance(gate, dict):
            continue
        check = {
            "name": gate.get("name"),
            "command": gate.get("command"),
            "status": gate.get("status"),
            "duration_ms": gate.get("duration_ms"),
            "evidence_path": gate.get("evidence_path"),
            "summary": gate.get("summary"),
            "failure_reason": gate.get("failure_reason"),
            "details": select_key_details(gate),
        }
        checks.append(check)
    return checks


def select_key_details(gate: dict[str, Any]) -> dict[str, Any]:
    name = str(gate.get("name") or "")
    details = gate.get("details") or {}
    if name == "performance":
        return {
            "warm_p95_ms": details.get("warm_p95_ms"),
            "warm_p95_under_3s": details.get("warm_p95_under_3s"),
            "slowest_warm_stage_by_p95": details.get("slowest_warm_stage_by_p95"),
        }
    if name == "eval_corpus_preflight":
        return {
            "lecardv2_status": details.get("lecardv2_status"),
            "product_local_status": details.get("product_local_status"),
            "overall_status": details.get("overall_status"),
        }
    if name == "product_eval":
        baseline = details.get("baseline") or {}
        current = details.get("current") or {}
        gray = details.get("gray_candidate") or {}
        return {
            "baseline_precision_at_5": baseline.get("precision_at_5"),
            "baseline_ndcg_at_10": baseline.get("ndcg_at_10"),
            "baseline_top10_hit_rate": baseline.get("top10_hit_rate"),
            "current_precision_at_5": current.get("precision_at_5"),
            "current_ndcg_at_10": current.get("ndcg_at_10"),
            "current_top10_hit_rate": current.get("top10_hit_rate"),
            "gray_candidate_eligible": gray.get("eligible"),
            "gray_candidate_reason": gray.get("reason"),
            "bad_cases_path": details.get("bad_cases_path"),
        }
    if name == "rerank_eval":
        decision = details.get("release_decision") or {}
        return {
            "current_rerank_eval_status": details.get("current_rerank_eval_status"),
            "product_smoke_status": details.get("product_smoke_status"),
            "release_decision": decision.get("decision"),
            "enable_new_rerank": decision.get("enable_new_rerank"),
            "reason": decision.get("reason"),
        }
    if name == "rollback":
        return {
            "flags": details.get("flags"),
            "recovery_within_60_seconds": details.get("recovery_within_60_seconds"),
            "max_rollback_elapsed_ms": details.get("max_rollback_elapsed_ms"),
        }
    if name == "db_smoke":
        return {
            "db_reachable": details.get("db_reachable"),
            "db_status": details.get("db_status"),
        }
    if name == "privacy":
        return {
            "scanned_file_count": details.get("scanned_file_count"),
            "violation_count": details.get("violation_count"),
        }
    return details if isinstance(details, dict) else {}


def build_conclusions(gate_report: dict[str, Any], checks: list[dict[str, Any]]) -> dict[str, Any]:
    by_name = {str(check["name"]): check for check in checks}
    all_main_chain = all(
        is_passed(by_name, name)
        for name in (
            "default_feature_flags",
            "backend_tests",
            "frontend_tests",
            "frontend_build",
            "day3_real_e2e",
            "runtime_preflight",
            "performance",
            "rollback",
            "privacy",
        )
    )

    comparable_eval = is_passed(by_name, "eval_corpus_preflight") and is_passed(by_name, "product_eval")
    product_eval_details = (by_name.get("product_eval", {}) or {}).get("details") or {}
    current_top10 = product_eval_details.get("current_top10_hit_rate")
    gray_candidate_eligible = bool(product_eval_details.get("gray_candidate_eligible"))
    db_reachable = bool(((by_name.get("db_smoke", {}) or {}).get("details") or {}).get("db_reachable"))
    db_status = ((by_name.get("db_smoke", {}) or {}).get("details") or {}).get("db_status")

    internal_decision = "GO" if all_main_chain else "NO_GO"
    internal_reason = (
        "主链路、性能、回滚、运行时一致性和隐私门禁通过；DB 仍为基础搜索可接受降级。"
        if internal_decision == "GO" and not db_reachable
        else "主链路、性能、回滚、运行时一致性和隐私门禁通过。"
        if internal_decision == "GO"
        else "主链路仍有未通过门禁，不能继续内部基础搜索。"
    )

    external_blockers = []
    if not all_main_chain:
        external_blockers.extend(failed_names(by_name, (
            "default_feature_flags",
            "backend_tests",
            "frontend_tests",
            "frontend_build",
            "day3_real_e2e",
            "runtime_preflight",
            "performance",
            "rollback",
            "privacy",
        )))
    if not comparable_eval:
        external_blockers.append("eval_corpus_preflight_or_product_eval")
    external_blockers.append("version_scope_internal_only")
    if not db_reachable and db_status:
        external_blockers.append("db_degraded_for_future_non_search_features")
    external_blockers = unique(external_blockers)

    new_rerank_blockers = []
    if not gray_candidate_eligible:
        new_rerank_blockers.append("product_eval_threshold_not_met")
    rerank_check = by_name.get("rerank_eval")
    if rerank_check and rerank_check.get("status") != "passed":
        new_rerank_blockers.append("rerank_eval_not_comparable")
    if not all_main_chain:
        new_rerank_blockers.extend(failed_names(by_name, (
            "runtime_preflight",
            "performance",
            "rollback",
            "privacy",
        )))
    new_rerank_blockers = unique(new_rerank_blockers)

    conclusions = {
        "overall_status": "go_internal_only" if internal_decision == "GO" else "blocked",
        "internal_basic_search": {
            "decision": internal_decision,
            "reason": internal_reason,
            "blocking_checks": [] if internal_decision == "GO" else failed_names(by_name, (
                "default_feature_flags",
                "backend_tests",
                "frontend_tests",
                "frontend_build",
                "day3_real_e2e",
                "runtime_preflight",
                "performance",
                "rollback",
                "privacy",
            )),
            "db_mode": "conditional_degraded" if not db_reachable else "healthy",
        },
        "external_gray": {
            "decision": "NO_GO",
            "reason": (
                "根据 M1.1 版本定位与 R6 规则，本轮只允许内部基础搜索；对外灰度仍不可开。"
                if all_main_chain and comparable_eval
                else "对外灰度门禁未满足，且 M1.1 版本范围本身不允许对外灰度。"
            ),
            "blocking_checks": external_blockers,
        },
        "new_rerank": {
            "decision": "NO_GO",
            "reason": (
                f"产品本地评测 Top10 hit rate={current_top10}，未达到 0.6 阈值；新排序仍不可开。"
                if current_top10 is not None
                else "没有充分的新排序正向证据；新排序仍不可开。"
            ),
            "blocking_checks": new_rerank_blockers,
        },
        "rule_inputs": {
            "main_chain_passed": all_main_chain,
            "performance_passed": is_passed(by_name, "performance"),
            "runtime_preflight_passed": is_passed(by_name, "runtime_preflight"),
            "rollback_passed": is_passed(by_name, "rollback"),
            "comparable_eval_ready": comparable_eval,
            "db_reachable": db_reachable,
            "db_status": db_status,
            "gray_candidate_eligible": gray_candidate_eligible,
        },
    }
    return conclusions


def build_path_check(checks: list[dict[str, Any]]) -> dict[str, Any]:
    missing_paths: list[str] = []
    for check in checks:
        path = check.get("evidence_path")
        if path and not Path(path).exists():
            missing_paths.append(str(path))
        bad_cases = (check.get("details") or {}).get("bad_cases_path")
        if bad_cases and not Path(bad_cases).exists():
            missing_paths.append(str(bad_cases))
    return {
        "status": "passed" if not missing_paths else "failed",
        "missing_paths": unique(missing_paths),
    }


def build_privacy_check(
    report: dict[str, Any],
    markdown: str,
    gate_report: dict[str, Any],
    final_json_path: Path,
    final_md_path: Path,
) -> dict[str, Any]:
    context = {"forbidden_terms": collect_forbidden_terms()}
    gate_paths = []
    for gate in gate_report.get("gates") or []:
        if isinstance(gate, dict) and gate.get("evidence_path"):
            gate_paths.append(Path(str(gate["evidence_path"])))
    scan = scan_inline_and_files(
        context,
        inline_payloads={
            str(final_json_path): json.dumps(report, ensure_ascii=False, indent=2),
            str(final_md_path): markdown,
        },
        paths=privacy_scan_paths(
            {"generated_evidence_paths": [str(final_json_path), str(final_md_path)]},
            [
                gate_like(check)
                for check in report.get("checks") or []
            ],
        ) + gate_paths,
    )
    return {
        "status": scan.get("status"),
        "scanned_file_count": len(scan.get("scanned_paths", [])),
        "violation_count": len(scan.get("violations", [])),
        "evidence_paths": [relative(path) or str(path) for path in gate_paths],
    }


def gate_like(check: dict[str, Any]) -> Any:
    class GateProxy:
        def __init__(self, payload: dict[str, Any]) -> None:
            self.evidence_path = payload.get("evidence_path")
            self.details = payload.get("details") or {}

    return GateProxy(check)


def failed_names(by_name: dict[str, dict[str, Any]], names: tuple[str, ...]) -> list[str]:
    result: list[str] = []
    for name in names:
        if not is_passed(by_name, name):
            result.append(name)
    return result


def is_passed(by_name: dict[str, dict[str, Any]], name: str) -> bool:
    return (by_name.get(name) or {}).get("status") == "passed"


def unique(values: list[str]) -> list[str]:
    ordered: list[str] = []
    for value in values:
        if value not in ordered:
            ordered.append(value)
    return ordered


def render_markdown(report: dict[str, Any]) -> str:
    conclusions = report["conclusions"]
    lines = [
        "# M1.1 Final Verification",
        "",
        f"- Generated at: `{report['generated_at']}`",
        f"- Source gate report: `{relative(report['source_gate_report']) or report['source_gate_report']}`",
        f"- Overall status: **{conclusions['overall_status']}**",
        "",
        "## Final Decisions",
        "",
        "| Scope | Decision | Reason | Blocking checks |",
        "| --- | --- | --- | --- |",
    ]
    for key, label in (
        ("internal_basic_search", "Internal basic search"),
        ("external_gray", "External gray"),
        ("new_rerank", "New rerank"),
    ):
        item = conclusions[key]
        blockers = ", ".join(item.get("blocking_checks") or []) or "-"
        lines.append(f"| {label} | **{item['decision']}** | {escape_md(item['reason'])} | {escape_md(blockers)} |")

    lines.extend([
        "",
        "## Check Summary",
        "",
        "| Check | Status | Duration ms | Command | Evidence | Key details | Failure reason |",
        "| --- | --- | ---: | --- | --- | --- | --- |",
    ])
    for check in report.get("checks") or []:
        evidence = relative(check.get("evidence_path")) or "-"
        details = json.dumps(check.get("details") or {}, ensure_ascii=False, sort_keys=True)
        lines.append(
            f"| {check['name']} | **{check['status']}** | {check['duration_ms']} | "
            f"`{escape_md(check['command'])}` | `{escape_md(evidence)}` | "
            f"{escape_md(details)} | {escape_md(check.get('failure_reason') or '-')} |"
        )

    lines.extend([
        "",
        "## Path And Privacy Checks",
        "",
        f"- Path check: **{report['path_check']['status']}**",
        f"- Privacy check: **{report.get('privacy_check', {}).get('status', 'unknown')}**",
        "",
        "## Notes",
        "",
        "- Default feature flags remain closed throughout the verification run.",
        "- DB degraded evidence is treated as search-only acceptable downgrade, not as support for history, feedback, favorites, or durable events.",
        "- This report contains commands, statuses, evidence paths, key metrics, and failure reasons only; it does not include raw query or raw case text.",
    ])
    return "\n".join(lines)


def escape_md(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


if __name__ == "__main__":
    raise SystemExit(main())
