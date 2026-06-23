"""M1.1 risk-fix release gate.

This script aggregates existing R1-R4 checks into a timestamped Go/No-Go
report. It intentionally does not change feature flags, business logic, vector
indexes, Chroma data, DB data, or evaluation corpora.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from time import perf_counter
from typing import Any


SCRIPT_PATH = Path(__file__).resolve()
API_ROOT = SCRIPT_PATH.parents[1]
PROJECT_ROOT = SCRIPT_PATH.parents[3]
WEB_ROOT = PROJECT_ROOT / "apps" / "web"
DOCS_DIR = PROJECT_ROOT / "docs" / "development"
DATA_EVAL_DIR = PROJECT_ROOT / "data" / "eval"
REPORT_PREFIX = "m1.1-risk-fix-gate"

REQUIRED_FALSE_FLAGS = (
    "ENABLE_QUERY_REWRITE",
    "ENABLE_WEIGHTED_RERANK",
    "ENABLE_SUMMARY",
    "ENABLE_EXPANDED_SEARCH",
)

FORBIDDEN_JSON_KEYS = {
    "query",
    "raw_query",
    "query_text",
    "raw_text",
    "case_facts",
    "case_fact",
    "fact",
    "facts",
    "content",
    "text",
    "matched_text",
    "chunk_text",
    "candidate_full_text",
}


@dataclass
class CommandResult:
    exit_code: int
    duration_ms: int
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    evidence_path: str | None = None


@dataclass
class GateItem:
    name: str
    status: str
    command: str
    duration_ms: int
    summary: str
    evidence_path: str | None = None
    failure_reason: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        row: dict[str, Any] = {
            "name": self.name,
            "status": self.status,
            "command": self.command,
            "duration_ms": self.duration_ms,
            "summary": self.summary,
        }
        if self.evidence_path:
            row["evidence_path"] = self.evidence_path
        if self.failure_reason:
            row["failure_reason"] = self.failure_reason
        if self.details:
            row["details"] = self.details
        return row


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the M1.1 risk-fix release gate.")
    parser.add_argument("--dry-run", action="store_true", help="Plan gates and write reports without executing commands.")
    parser.add_argument(
        "--no-fail-on-no-go",
        action="store_true",
        help="Return 0 when reports are written even if Go/No-Go is negative.",
    )
    parser.add_argument("--timestamp", default="", help="Optional report timestamp suffix.")
    parser.add_argument("--timeout-seconds", type=int, default=900, help="Default command timeout.")
    parser.add_argument("--e2e-timeout-seconds", type=int, default=180, help="Day3 E2E command timeout.")
    args = parser.parse_args()

    timestamp = args.timestamp or datetime.now().strftime("%Y%m%d-%H%M%S")
    DOCS_DIR.mkdir(parents=True, exist_ok=True)

    context = {
        "timestamp": timestamp,
        "dry_run": args.dry_run,
        "timeout_seconds": args.timeout_seconds,
        "e2e_timeout_seconds": args.e2e_timeout_seconds,
        "forbidden_terms": collect_forbidden_terms(),
        "generated_evidence_paths": [],
    }

    gates: list[GateItem] = []
    gates.append(default_feature_flags_gate(context))
    gates.append(run_backend_tests(context))
    gates.append(run_frontend_tests(context))
    gates.append(run_frontend_build(context))
    gates.append(run_day3_e2e(context))
    gates.append(run_runtime_preflight(context))
    gates.append(run_db_smoke(context))
    gates.append(run_performance_smoke(context))
    gates.append(run_eval_preflight(context))
    gates.append(run_rerank_eval(context))
    gates.append(run_product_eval(context))
    gates.append(run_rollback_drill(context))

    report_path = DOCS_DIR / f"{REPORT_PREFIX}-{timestamp}.json"
    md_path = DOCS_DIR / f"{REPORT_PREFIX}-{timestamp}.md"

    decisions = compute_decisions(gates)
    draft_report = build_report(context, gates, decisions, report_path, md_path)
    draft_md = render_markdown(draft_report)
    privacy_gate = privacy_scan_gate(
        context,
        gates,
        draft_json=json.dumps(draft_report, ensure_ascii=False, indent=2),
        draft_md=draft_md,
    )
    gates.append(privacy_gate)
    decisions = compute_decisions(gates)
    final_report = build_report(context, gates, decisions, report_path, md_path)
    final_md = render_markdown(final_report)

    final_privacy_check = scan_inline_and_files(
        context,
        inline_payloads={
            str(report_path): json.dumps(final_report, ensure_ascii=False, indent=2),
            str(md_path): final_md,
        },
        paths=privacy_scan_paths(context, gates),
    )
    if final_privacy_check["status"] != "passed" and privacy_gate.status == "passed":
        gates[-1] = GateItem(
            name="privacy",
            status="failed",
            command="internal privacy scan",
            duration_ms=privacy_gate.duration_ms,
            summary="Final gate report privacy scan failed.",
            failure_reason="generated_gate_report_contains_forbidden_raw_content",
            details=final_privacy_check,
        )
        decisions = compute_decisions(gates)
        final_report = build_report(context, gates, decisions, report_path, md_path)
        final_md = render_markdown(final_report)

    report_path.write_text(json.dumps(final_report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md_path.write_text(final_md + "\n", encoding="utf-8")

    print(json.dumps(
        {
            "status": final_report["overall_status"],
            "json_report": str(report_path),
            "markdown_report": str(md_path),
            "decisions": final_report["decisions"],
        },
        ensure_ascii=False,
        indent=2,
    ))
    if args.no_fail_on_no_go or args.dry_run:
        return 0
    return 0 if final_report["overall_status"] == "go" else 1


def safe_env() -> dict[str, str]:
    env = os.environ.copy()
    project_env = load_env_file(PROJECT_ROOT / ".env")
    env.update(project_env)
    for flag in REQUIRED_FALSE_FLAGS:
        env[flag] = "false"
    env["VITE_ENABLE_EXPANDED_SEARCH"] = "false"
    return env


def command_text(command: list[str] | str) -> str:
    if isinstance(command, str):
        return command
    return " ".join(command)


def run_command(
    *,
    command: list[str] | str,
    cwd: Path,
    timeout_seconds: int,
    evidence_name: str,
    context: dict[str, Any],
    dry_run_summary: str,
) -> CommandResult:
    evidence_path = DOCS_DIR / f"{REPORT_PREFIX}-{evidence_name}-{context['timestamp']}.json"
    command_display = command_text(command)
    if context["dry_run"]:
        return CommandResult(
            exit_code=0,
            duration_ms=0,
            timed_out=False,
            evidence_path=str(evidence_path),
        )

    started = perf_counter()
    try:
        completed = subprocess.run(
            command_display,
            cwd=str(cwd),
            env=safe_env(),
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            encoding="utf-8",
            errors="replace",
        )
        duration_ms = int((perf_counter() - started) * 1000)
        result = CommandResult(
            exit_code=int(completed.returncode),
            duration_ms=duration_ms,
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
            timed_out=False,
            evidence_path=str(evidence_path),
        )
    except subprocess.TimeoutExpired as exc:
        duration_ms = int((perf_counter() - started) * 1000)
        result = CommandResult(
            exit_code=124,
            duration_ms=duration_ms,
            stdout=exc.stdout if isinstance(exc.stdout, str) else "",
            stderr=exc.stderr if isinstance(exc.stderr, str) else "",
            timed_out=True,
            evidence_path=str(evidence_path),
        )

    evidence = {
        "command": command_display,
        "cwd": str(cwd),
        "exit_code": result.exit_code,
        "duration_ms": result.duration_ms,
        "timed_out": result.timed_out,
        "stdout_line_count": len(result.stdout.splitlines()),
        "stderr_line_count": len(result.stderr.splitlines()),
        "stdout_sha256": sha256_text(result.stdout),
        "stderr_sha256": sha256_text(result.stderr),
        "stdout_bytes": len(result.stdout.encode("utf-8", errors="ignore")),
        "stderr_bytes": len(result.stderr.encode("utf-8", errors="ignore")),
        "output_policy": "stdout/stderr are hashed only to avoid writing raw query or case facts.",
        "dry_run_summary": dry_run_summary if context["dry_run"] else None,
    }
    evidence_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    context["generated_evidence_paths"].append(str(evidence_path))
    return result


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="ignore")).hexdigest()


def relative(path: str | Path | None) -> str | None:
    if not path:
        return None
    try:
        return str(Path(path).resolve().relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def required_paths_missing(paths: list[Path]) -> list[str]:
    return [str(path) for path in paths if not path.exists()]


def blocked_for_missing(name: str, command: str, missing: list[str]) -> GateItem:
    return GateItem(
        name=name,
        status="blocked",
        command=command,
        duration_ms=0,
        summary="Required script or working directory is missing.",
        failure_reason="missing_required_path",
        details={"missing_paths": missing},
    )


def default_feature_flags_gate(context: dict[str, Any]) -> GateItem:
    started = perf_counter()
    command = "inspect Settings defaults and .env/.env.example feature flags"
    details: dict[str, Any] = {
        "required_false_flags": list(REQUIRED_FALSE_FLAGS),
        "settings_defaults": {},
        "env_files": {},
    }
    failures: list[str] = []

    config_path = API_ROOT / "app" / "core" / "config.py"
    config_text = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
    for flag in REQUIRED_FALSE_FLAGS:
        match = re.search(rf"\b{flag}\s*:\s*bool\s*=\s*(True|False)", config_text)
        value = match.group(1) if match else None
        details["settings_defaults"][flag] = value
        if value != "False":
            failures.append(f"Settings default for {flag} is not False")

    for env_name in (".env", ".env.example"):
        env_path = PROJECT_ROOT / env_name
        parsed = parse_env_flags(env_path)
        details["env_files"][env_name] = parsed
        for flag in REQUIRED_FALSE_FLAGS:
            if flag in parsed and parsed[flag].lower() != "false":
                failures.append(f"{env_name} sets {flag}={parsed[flag]}")
        if "VITE_ENABLE_EXPANDED_SEARCH" in parsed and parsed["VITE_ENABLE_EXPANDED_SEARCH"].lower() != "false":
            failures.append(f"{env_name} sets VITE_ENABLE_EXPANDED_SEARCH={parsed['VITE_ENABLE_EXPANDED_SEARCH']}")

    status = "failed" if failures else "passed"
    return GateItem(
        name="default_feature_flags",
        status=status,
        command=command,
        duration_ms=int((perf_counter() - started) * 1000),
        summary="Default enhancement flags remain closed." if status == "passed" else "Default enhancement flags are not all closed.",
        failure_reason="; ".join(failures) if failures else None,
        details=details,
    )


def parse_env_flags(path: Path) -> dict[str, str]:
    values = load_env_file(path)
    return {
        key: value
        for key, value in values.items()
        if key in REQUIRED_FALSE_FLAGS or key == "VITE_ENABLE_EXPANDED_SEARCH"
    }


def load_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def run_backend_tests(context: dict[str, Any]) -> GateItem:
    command = "python -m pytest"
    missing = required_paths_missing([API_ROOT / "tests", API_ROOT / "pyproject.toml"])
    if missing:
        return blocked_for_missing("backend_tests", command, missing)
    if context["dry_run"]:
        return dry_run_gate("backend_tests", command, "Would run the backend pytest suite.")
    result = run_command(
        command=command,
        cwd=API_ROOT,
        timeout_seconds=context["timeout_seconds"],
        evidence_name="backend-tests",
        context=context,
        dry_run_summary="Would run backend pytest.",
    )
    return command_gate_result(
        name="backend_tests",
        command=command,
        result=result,
        pass_summary="Backend pytest completed successfully.",
        fail_summary="Backend pytest did not complete successfully.",
    )


def run_frontend_tests(context: dict[str, Any]) -> GateItem:
    command = "npm test"
    missing = required_paths_missing([WEB_ROOT / "package.json"])
    if missing:
        return blocked_for_missing("frontend_tests", command, missing)
    if context["dry_run"]:
        return dry_run_gate("frontend_tests", command, "Would run the frontend npm test suite.")
    result = run_command(
        command=command,
        cwd=WEB_ROOT,
        timeout_seconds=context["timeout_seconds"],
        evidence_name="frontend-tests",
        context=context,
        dry_run_summary="Would run frontend tests.",
    )
    return command_gate_result(
        name="frontend_tests",
        command=command,
        result=result,
        pass_summary="Frontend npm test completed successfully.",
        fail_summary="Frontend npm test did not complete successfully.",
    )


def run_frontend_build(context: dict[str, Any]) -> GateItem:
    command = "npm run build"
    missing = required_paths_missing([WEB_ROOT / "package.json"])
    if missing:
        return blocked_for_missing("frontend_build", command, missing)
    if context["dry_run"]:
        return dry_run_gate("frontend_build", command, "Would run the frontend production build.")
    result = run_command(
        command=command,
        cwd=WEB_ROOT,
        timeout_seconds=context["timeout_seconds"],
        evidence_name="frontend-build",
        context=context,
        dry_run_summary="Would run frontend build.",
    )
    return command_gate_result(
        name="frontend_build",
        command=command,
        result=result,
        pass_summary="Frontend production build completed successfully.",
        fail_summary="Frontend production build did not complete successfully.",
    )


def run_day3_e2e(context: dict[str, Any]) -> GateItem:
    command = "npm run smoke:day3:7.1"
    missing = required_paths_missing([WEB_ROOT / "package.json", WEB_ROOT / "scripts" / "day3-7.1-e2e-smoke.mjs"])
    if missing:
        return blocked_for_missing("day3_real_e2e", command, missing)
    if context["dry_run"]:
        return dry_run_gate("day3_real_e2e", command, "Would run Day3 real E2E without --mock.")
    result = run_command(
        command=command,
        cwd=WEB_ROOT,
        timeout_seconds=context["e2e_timeout_seconds"],
        evidence_name="day3-real-e2e",
        context=context,
        dry_run_summary="Would run real E2E smoke without mock mode.",
    )
    return command_gate_result(
        name="day3_real_e2e",
        command=command,
        result=result,
        pass_summary="Day3 real E2E smoke passed.",
        fail_summary="Day3 real E2E smoke failed or required live services were unavailable.",
    )


def run_runtime_preflight(context: dict[str, Any]) -> GateItem:
    script = API_ROOT / "scripts" / "runtime_preflight.py"
    command = f'python "{script}"'
    missing = required_paths_missing([script])
    if missing:
        return blocked_for_missing("runtime_preflight", command, missing)
    if context["dry_run"]:
        return dry_run_gate("runtime_preflight", command, "Would compare live /health with source TestClient /health.")
    result = run_command(
        command=command,
        cwd=PROJECT_ROOT,
        timeout_seconds=context["timeout_seconds"],
        evidence_name="runtime-preflight",
        context=context,
        dry_run_summary="Would run runtime preflight.",
    )
    return command_gate_result(
        name="runtime_preflight",
        command=command,
        result=result,
        pass_summary="Live /health matches source /health and includes feature_flags.",
        fail_summary="Runtime preflight failed; live API may be stopped, stale, or inconsistent.",
    )


def run_db_smoke(context: dict[str, Any]) -> GateItem:
    script = API_ROOT / "scripts" / "db_smoke.py"
    command = f'python "{script}"'
    missing = required_paths_missing([script])
    if missing:
        return blocked_for_missing("db_smoke", command, missing)
    if context["dry_run"]:
        return dry_run_gate("db_smoke", command, "Would run the R2 DB smoke.")
    result = run_command(
        command=command,
        cwd=PROJECT_ROOT,
        timeout_seconds=context["timeout_seconds"],
        evidence_name="db-smoke-command",
        context=context,
        dry_run_summary="Would run DB smoke.",
    )
    parsed = parse_json_from_stdout(result.stdout)
    evidence_path = DOCS_DIR / f"{REPORT_PREFIX}-db-smoke-{context['timestamp']}.json"
    if parsed is not None:
        evidence = {
            "command": command,
            "exit_code": result.exit_code,
            "duration_ms": result.duration_ms,
            "report": parsed,
        }
        evidence_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        context["generated_evidence_paths"].append(str(evidence_path))
        status = "passed" if parsed.get("status") == "ok" and result.exit_code == 0 else "failed"
        connection = ((parsed.get("checks") or {}).get("connection") or {}).get("detail") or {}
        summary = "DB smoke passed." if status == "passed" else "DB smoke is degraded or failed."
        return GateItem(
            name="db_smoke",
            status=status,
            command=command,
            duration_ms=result.duration_ms,
            summary=summary,
            evidence_path=str(evidence_path),
            failure_reason=None if status == "passed" else str(connection.get("reason") or parsed.get("status") or "db_smoke_failed"),
            details={
                "db_reachable": bool(connection.get("reachable")),
                "db_status": parsed.get("status"),
            },
        )
    return command_gate_result(
        name="db_smoke",
        command=command,
        result=result,
        pass_summary="DB smoke passed.",
        fail_summary="DB smoke did not return a parseable report.",
    )


def run_performance_smoke(context: dict[str, Any]) -> GateItem:
    script = API_ROOT / "scripts" / "day3_7_3_performance_smoke.py"
    out = DOCS_DIR / f"{REPORT_PREFIX}-performance-smoke-{context['timestamp']}.json"
    command = f'python "{script}" --out "{out}"'
    missing = required_paths_missing([script])
    if missing:
        return blocked_for_missing("performance", command, missing)
    if context["dry_run"]:
        return dry_run_gate("performance", command, "Would run R3 performance smoke and require warm P95 < 3000ms.")
    result = run_command(
        command=command,
        cwd=PROJECT_ROOT,
        timeout_seconds=context["timeout_seconds"],
        evidence_name="performance-command",
        context=context,
        dry_run_summary="Would run performance smoke.",
    )
    if out.exists():
        context["generated_evidence_paths"].append(str(out))
    report = read_json_if_exists(out)
    if result.exit_code != 0:
        return command_gate_result(
            name="performance",
            command=command,
            result=result,
            pass_summary="Performance smoke passed.",
            fail_summary="Performance smoke command failed.",
            evidence_override=str(out) if out.exists() else result.evidence_path,
        )
    if not report:
        return GateItem(
            name="performance",
            status="failed",
            command=command,
            duration_ms=result.duration_ms,
            summary="Performance smoke did not produce a parseable JSON report.",
            evidence_path=result.evidence_path,
            failure_reason="missing_or_invalid_performance_report",
        )
    warm_p95 = (((report.get("api") or {}).get("warm_response_total_duration_ms") or {}).get("p95"))
    passed = isinstance(warm_p95, (int, float)) and warm_p95 < 3000
    return GateItem(
        name="performance",
        status="passed" if passed else "failed",
        command=command,
        duration_ms=result.duration_ms,
        summary=f"Warm P95 is {warm_p95}ms." if warm_p95 is not None else "Warm P95 is unavailable.",
        evidence_path=str(out),
        failure_reason=None if passed else "warm_p95_not_under_3000ms",
        details={
            "warm_p95_ms": warm_p95,
            "warm_p95_under_3s": bool(passed),
            "slowest_warm_stage_by_p95": report.get("slowest_warm_stage_by_p95"),
        },
    )


def run_eval_preflight(context: dict[str, Any]) -> GateItem:
    script = API_ROOT / "scripts" / "eval_corpus_preflight.py"
    out = DOCS_DIR / f"{REPORT_PREFIX}-eval-preflight-{context['timestamp']}.json"
    command = f'python "{script}" --out "{out}" --allow-blocked'
    missing = required_paths_missing([script])
    if missing:
        return blocked_for_missing("eval_corpus_preflight", command, missing)
    if context["dry_run"]:
        return dry_run_gate("eval_corpus_preflight", command, "Would run R4 eval corpus preflight.")
    result = run_command(
        command=command,
        cwd=PROJECT_ROOT,
        timeout_seconds=context["timeout_seconds"],
        evidence_name="eval-preflight-command",
        context=context,
        dry_run_summary="Would run eval corpus preflight.",
    )
    if out.exists():
        context["generated_evidence_paths"].append(str(out))
    report = read_json_if_exists(out)
    if result.exit_code != 0 and not report:
        return command_gate_result(
            name="eval_corpus_preflight",
            command=command,
            result=result,
            pass_summary="Eval corpus preflight passed.",
            fail_summary="Eval corpus preflight command failed.",
            evidence_override=result.evidence_path,
        )
    if not report:
        return GateItem(
            name="eval_corpus_preflight",
            status="failed",
            command=command,
            duration_ms=result.duration_ms,
            summary="Eval corpus preflight did not produce a parseable JSON report.",
            evidence_path=result.evidence_path,
            failure_reason="missing_or_invalid_eval_preflight_report",
        )
    lecard_status = ((report.get("lecardv2") or {}).get("status"))
    product_status = ((report.get("product_local") or {}).get("status"))
    comparable_line_ok = lecard_status == "ok" or product_status == "ok"
    return GateItem(
        name="eval_corpus_preflight",
        status="passed" if comparable_line_ok else "blocked",
        command=command,
        duration_ms=result.duration_ms,
        summary=f"Comparable eval line status: lecardv2={lecard_status}, product_local={product_status}.",
        evidence_path=str(out),
        failure_reason=None if comparable_line_ok else "no_comparable_eval_line_ready",
        details={
            "lecardv2_status": lecard_status,
            "product_local_status": product_status,
            "overall_status": report.get("status"),
        },
    )


def run_product_eval(context: dict[str, Any]) -> GateItem:
    out = DOCS_DIR / f"{REPORT_PREFIX}-product-eval-{context['timestamp']}.json"
    bad_cases = DOCS_DIR / f"{REPORT_PREFIX}-product-bad-cases-{context['timestamp']}.json"
    command = f'python -m app.eval.product_eval --out "{out}" --bad-cases-out "{bad_cases}"'
    missing = required_paths_missing([
        API_ROOT / "app" / "eval" / "product_eval.py",
        DATA_EVAL_DIR / "product_eval_queries.jsonl",
        DATA_EVAL_DIR / "product_eval_qrels.jsonl",
        PROJECT_ROOT / "data" / "processed" / "cases.jsonl",
        PROJECT_ROOT / "data" / "processed" / "chunks.jsonl",
    ])
    if missing:
        return blocked_for_missing("product_eval", command, missing)
    if context["dry_run"]:
        return dry_run_gate("product_eval", command, "Would run R4 product-local evaluation.")
    result = run_command(
        command=command,
        cwd=API_ROOT,
        timeout_seconds=context["timeout_seconds"],
        evidence_name="product-eval-command",
        context=context,
        dry_run_summary="Would run product eval.",
    )
    for path in (out, bad_cases):
        if path.exists():
            context["generated_evidence_paths"].append(str(path))
    report = read_json_if_exists(out)
    if result.exit_code != 0 and not report:
        return command_gate_result(
            name="product_eval",
            command=command,
            result=result,
            pass_summary="Product-local evaluation passed.",
            fail_summary="Product-local evaluation command failed.",
            evidence_override=result.evidence_path,
        )
    if not report:
        return GateItem(
            name="product_eval",
            status="failed",
            command=command,
            duration_ms=result.duration_ms,
            summary="Product-local evaluation did not produce a parseable JSON report.",
            evidence_path=result.evidence_path,
            failure_reason="missing_or_invalid_product_eval_report",
        )
    baseline_count = ((report.get("baseline") or {}).get("evaluated_query_count"))
    current_count = ((report.get("current") or {}).get("evaluated_query_count"))
    blocked = (report.get("gray_candidate") or {}).get("blocked_reasons") or []
    comparable = bool(baseline_count and current_count and not blocked)
    gray = (report.get("gray_candidate") or {})
    return GateItem(
        name="product_eval",
        status="passed" if comparable else "blocked",
        command=command,
        duration_ms=result.duration_ms,
        summary=(
            f"Product eval comparable queries: baseline={baseline_count}, current={current_count}; "
            f"new-rerank eligible={bool(gray.get('eligible'))}."
        ),
        evidence_path=str(out),
        failure_reason=None if comparable else "product_eval_blocked_or_no_evaluated_queries",
        details={
            "baseline": report.get("baseline"),
            "current": report.get("current"),
            "metric_delta": report.get("metric_delta"),
            "m13_regression_gate": report.get("m13_regression_gate"),
            "gray_candidate": gray,
            "bad_cases_path": str(bad_cases) if bad_cases.exists() else None,
        },
    )


def run_rerank_eval(context: dict[str, Any]) -> GateItem:
    out = DOCS_DIR / f"{REPORT_PREFIX}-rerank-eval-{context['timestamp']}.json"
    command = f'python -m app.eval.day3_rerank_eval --out "{out}"'
    missing = required_paths_missing([
        API_ROOT / "app" / "eval" / "day3_rerank_eval.py",
        DATA_EVAL_DIR / "lecardv2_queries.jsonl",
        DATA_EVAL_DIR / "lecardv2_qrels.jsonl",
    ])
    if missing:
        return blocked_for_missing("rerank_eval", command, missing)
    if context["dry_run"]:
        return dry_run_gate("rerank_eval", command, "Would run the Day3 rerank evaluation runner.")
    result = run_command(
        command=command,
        cwd=API_ROOT,
        timeout_seconds=context["timeout_seconds"],
        evidence_name="rerank-eval-command",
        context=context,
        dry_run_summary="Would run rerank eval.",
    )
    if out.exists():
        context["generated_evidence_paths"].append(str(out))
    report = read_json_if_exists(out)
    if result.exit_code != 0 and not report:
        return command_gate_result(
            name="rerank_eval",
            command=command,
            result=result,
            pass_summary="Rerank evaluation passed.",
            fail_summary="Rerank evaluation command failed.",
            evidence_override=result.evidence_path,
        )
    if not report:
        return GateItem(
            name="rerank_eval",
            status="failed",
            command=command,
            duration_ms=result.duration_ms,
            summary="Rerank evaluation did not produce a parseable JSON report.",
            evidence_path=result.evidence_path,
            failure_reason="missing_or_invalid_rerank_eval_report",
        )
    current_eval = (report.get("current_rerank_eval") or {})
    release_decision = (report.get("release_decision") or {})
    comparable = current_eval.get("status") == "ok"
    return GateItem(
        name="rerank_eval",
        status="passed" if comparable else "blocked",
        command=command,
        duration_ms=result.duration_ms,
        summary=(
            f"Rerank eval status={current_eval.get('status')}, release_decision={release_decision.get('decision')}."
        ),
        evidence_path=str(out),
        failure_reason=None if comparable else str(current_eval.get("status") or "rerank_eval_blocked"),
        details={
            "release_decision": release_decision,
            "current_rerank_eval_status": current_eval.get("status"),
            "product_smoke_status": (report.get("current_product_smoke") or {}).get("status"),
        },
    )


def run_rollback_drill(context: dict[str, Any]) -> GateItem:
    script = API_ROOT / "scripts" / "day3_7_5_rollback_drill.py"
    out = DOCS_DIR / f"{REPORT_PREFIX}-rollback-drill-{context['timestamp']}.json"
    command = f'python "{script}" --out "{out}"'
    missing = required_paths_missing([script])
    if missing:
        return blocked_for_missing("rollback", command, missing)
    if context["dry_run"]:
        return dry_run_gate("rollback", command, "Would run R4 rollback drill for four feature flags.")
    result = run_command(
        command=command,
        cwd=PROJECT_ROOT,
        timeout_seconds=context["timeout_seconds"],
        evidence_name="rollback-command",
        context=context,
        dry_run_summary="Would run rollback drill.",
    )
    if out.exists():
        context["generated_evidence_paths"].append(str(out))
    report = read_json_if_exists(out)
    if result.exit_code != 0 and not report:
        return command_gate_result(
            name="rollback",
            command=command,
            result=result,
            pass_summary="Rollback drill passed.",
            fail_summary="Rollback drill command failed.",
            evidence_override=result.evidence_path,
        )
    if not report:
        return GateItem(
            name="rollback",
            status="failed",
            command=command,
            duration_ms=result.duration_ms,
            summary="Rollback drill did not produce a parseable JSON report.",
            evidence_path=result.evidence_path,
            failure_reason="missing_or_invalid_rollback_report",
        )
    scenarios = report.get("scenarios") or []
    flags = {item.get("flag"): item.get("status") for item in scenarios if isinstance(item, dict)}
    all_flags = all(flags.get(flag) == "passed" for flag in REQUIRED_FALSE_FLAGS)
    recovery_ok = bool(report.get("recovery_within_60_seconds")) and int(report.get("max_rollback_elapsed_ms") or 60000) < 60000
    passed = report.get("status") == "passed" and all_flags and recovery_ok
    return GateItem(
        name="rollback",
        status="passed" if passed else "failed",
        command=command,
        duration_ms=result.duration_ms,
        summary=f"Rollback drill status={report.get('status')}, max_restore={report.get('max_rollback_elapsed_ms')}ms.",
        evidence_path=str(out),
        failure_reason=None if passed else "rollback_drill_failed_or_restore_over_60s",
        details={
            "flags": flags,
            "recovery_within_60_seconds": report.get("recovery_within_60_seconds"),
            "max_rollback_elapsed_ms": report.get("max_rollback_elapsed_ms"),
        },
    )


def dry_run_gate(name: str, command: str, summary: str) -> GateItem:
    return GateItem(
        name=name,
        status="blocked",
        command=command,
        duration_ms=0,
        summary=summary,
        failure_reason="dry_run_not_executed",
    )


def command_gate_result(
    *,
    name: str,
    command: str,
    result: CommandResult,
    pass_summary: str,
    fail_summary: str,
    evidence_override: str | None = None,
) -> GateItem:
    passed = result.exit_code == 0 and not result.timed_out
    reason = None
    if not passed:
        reason = "command_timed_out" if result.timed_out else f"command_exit_code_{result.exit_code}"
    return GateItem(
        name=name,
        status="passed" if passed else "failed",
        command=command,
        duration_ms=result.duration_ms,
        summary=pass_summary if passed else fail_summary,
        evidence_path=evidence_override or result.evidence_path,
        failure_reason=reason,
        details={
            "exit_code": result.exit_code,
            "timed_out": result.timed_out,
        },
    )


def read_json_if_exists(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def parse_json_from_stdout(stdout: str) -> dict[str, Any] | None:
    text = stdout.strip()
    if not text:
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"(\{.*\})", text, flags=re.DOTALL)
        if not match:
            return None
        try:
            payload = json.loads(match.group(1))
        except json.JSONDecodeError:
            return None
    return payload if isinstance(payload, dict) else None


def gate_map(gates: list[GateItem]) -> dict[str, GateItem]:
    return {gate.name: gate for gate in gates}


def is_passed(gates_by_name: dict[str, GateItem], name: str) -> bool:
    return gates_by_name.get(name) is not None and gates_by_name[name].status == "passed"


def compute_decisions(gates: list[GateItem]) -> dict[str, Any]:
    by_name = gate_map(gates)
    main_chain_names = [
        "default_feature_flags",
        "backend_tests",
        "frontend_tests",
        "frontend_build",
        "day3_real_e2e",
        "runtime_preflight",
        "performance",
        "rollback",
    ]
    privacy_ok = "privacy" not in by_name or is_passed(by_name, "privacy")
    main_chain_ok = all(is_passed(by_name, name) for name in main_chain_names) and privacy_ok
    eval_comparable = is_passed(by_name, "eval_corpus_preflight") and (
        is_passed(by_name, "product_eval") or is_passed(by_name, "rerank_eval")
    )
    db_ok = is_passed(by_name, "db_smoke")
    performance_ok = is_passed(by_name, "performance")
    rollback_ok = is_passed(by_name, "rollback")
    runtime_ok = is_passed(by_name, "runtime_preflight")

    product_eval = by_name.get("product_eval")
    gray_candidate = ((product_eval.details or {}).get("gray_candidate") if product_eval else {}) or {}
    rerank_eval = by_name.get("rerank_eval")
    rerank_release_decision = ((rerank_eval.details or {}).get("release_decision") if rerank_eval else {}) or {}
    gray_hard_gate_passed = bool(gray_candidate.get("grayCandidateHardGatePassed"))
    weighted_gray_candidate = bool(gray_candidate.get("weightedRerankGrayCandidate"))
    aggregate_or_standard_positive = bool(gray_candidate.get("eligible")) or bool(
        rerank_release_decision.get("enable_new_rerank")
    )
    new_rerank_positive = (weighted_gray_candidate or aggregate_or_standard_positive) and gray_hard_gate_passed
    new_rerank_reason = str(
        gray_candidate.get("reason")
        or "; ".join(gray_candidate.get("hardGateFailedReasons") or [])
        or rerank_release_decision.get("reason")
        or "No positive new-rerank evidence was produced."
    )

    eval_blockers: list[str] = []
    if not is_passed(by_name, "eval_corpus_preflight"):
        eval_blockers.append("eval_corpus_preflight")
    if not (is_passed(by_name, "product_eval") or is_passed(by_name, "rerank_eval")):
        eval_blockers.extend(["product_eval", "rerank_eval"])
    blockers = {
        "main_chain": failed_gate_names(gates, main_chain_names),
        "eval": eval_blockers,
        "db": [] if db_ok else failed_gate_names(gates, ["db_smoke"]),
        "privacy": [] if privacy_ok else ["privacy"],
    }

    internal_go = main_chain_ok
    external_go = main_chain_ok and eval_comparable
    new_rerank_go = external_go and new_rerank_positive

    return {
        "internal_basic_search": {
            "decision": "GO" if internal_go else "NO_GO",
            "reason": (
                "Main chain, performance, rollback, runtime, privacy, and default flag gates passed."
                if internal_go
                else "Internal basic search is blocked by one or more main-chain gates."
            ),
            "blocking_gates": blockers["main_chain"] + blockers["privacy"],
        },
        "external_gray": {
            "decision": "GO" if external_go else "NO_GO",
            "reason": (
                "Main chain, performance, comparable eval, rollback, runtime, privacy, and default flags passed."
                if external_go
                else "External gray is blocked by main-chain, eval, runtime, rollback, performance, or privacy gates."
            ),
            "blocking_gates": sorted(set(blockers["main_chain"] + blockers["eval"] + blockers["privacy"])),
        },
        "new_rerank": {
            "decision": "GO" if new_rerank_go else "NO_GO",
            "reason": (
                "Offline evaluation marks weighted rerank as eligible."
                if new_rerank_go
                else new_rerank_reason
            ),
            "blocking_gates": sorted(set(blockers["main_chain"] + blockers["eval"] + blockers["privacy"])),
            "eval_gray_candidate_eligible": new_rerank_positive,
            "m13_hard_gate_passed": gray_hard_gate_passed,
        },
        "rule_inputs": {
            "main_chain_ok": main_chain_ok,
            "runtime_ok": runtime_ok,
            "performance_ok": performance_ok,
            "eval_comparable": eval_comparable,
            "rollback_ok": rollback_ok,
            "db_ok": db_ok,
            "privacy_ok": privacy_ok,
            "new_rerank_positive": new_rerank_positive,
            "m13_hard_gate_passed": gray_hard_gate_passed,
        },
        "db_note": {
            "decision": "GO" if db_ok else "CONDITIONAL",
            "reason": (
                "DB smoke passed."
                if db_ok
                else "DB smoke failed/degraded. This does not block search-only release decisions here, but it blocks any next version that requires history, feedback, favorites, or durable events."
            ),
            "blocking_gates": [] if db_ok else blockers["db"],
        },
    }


def failed_gate_names(gates: list[GateItem], names: list[str]) -> list[str]:
    by_name = gate_map(gates)
    result: list[str] = []
    for name in names:
        gate = by_name.get(name)
        if gate is None or gate.status != "passed":
            result.append(name)
    return result


def overall_status(gates: list[GateItem], decisions: dict[str, Any]) -> str:
    if decisions["external_gray"]["decision"] == "GO":
        return "go"
    if decisions["internal_basic_search"]["decision"] == "GO":
        return "partial_go"
    if any(gate.status == "blocked" for gate in gates):
        return "blocked"
    return "no_go"


def build_report(
    context: dict[str, Any],
    gates: list[GateItem],
    decisions: dict[str, Any],
    report_path: Path,
    md_path: Path,
) -> dict[str, Any]:
    return {
        "version": "m1_1_r5_release_gate_v1",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "project_root": str(PROJECT_ROOT),
        "dry_run": bool(context["dry_run"]),
        "overall_status": overall_status(gates, decisions),
        "reports": {
            "json": str(report_path),
            "markdown": str(md_path),
        },
        "policy": {
            "does_not_change_feature_flags": True,
            "does_not_rebuild_vector_index": True,
            "does_not_clear_chroma": True,
            "does_not_delete_existing_data": True,
            "command_output_policy": "stdout/stderr are not written verbatim to gate evidence.",
        },
        "required_default_flags": {flag: False for flag in REQUIRED_FALSE_FLAGS},
        "gates": [gate.as_dict() for gate in gates],
        "decisions": decisions,
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# M1.1 Risk-Fix Release Gate",
        "",
        f"- Generated at: `{report['generated_at']}`",
        f"- Overall status: **{report['overall_status']}**",
        f"- Dry run: `{str(report['dry_run']).lower()}`",
        "",
        "## Go/No-Go",
        "",
        "| Scope | Decision | Reason | Blocking gates |",
        "| --- | --- | --- | --- |",
    ]
    for key, label in (
        ("internal_basic_search", "Internal basic search"),
        ("external_gray", "External gray"),
        ("new_rerank", "New rerank"),
    ):
        item = report["decisions"][key]
        blockers = ", ".join(item.get("blocking_gates") or []) or "-"
        lines.append(f"| {label} | **{item['decision']}** | {escape_md(item['reason'])} | {escape_md(blockers)} |")

    lines.extend([
        "",
        "## Gate Items",
        "",
        "| Gate | Status | Duration ms | Evidence | Summary |",
        "| --- | --- | ---: | --- | --- |",
    ])
    for gate in report["gates"]:
        evidence = relative(gate.get("evidence_path")) or "-"
        summary = gate["summary"]
        if gate.get("failure_reason"):
            summary = f"{summary} Failure reason: {gate['failure_reason']}"
        lines.append(
            f"| {gate['name']} | **{gate['status']}** | {gate['duration_ms']} | `{escape_md(evidence)}` | {escape_md(summary)} |"
        )

    lines.extend([
        "",
        "## Default Flags",
        "",
        "| Flag | Required value |",
        "| --- | --- |",
    ])
    for flag, value in report["required_default_flags"].items():
        lines.append(f"| `{flag}` | `{str(value).lower()}` |")

    lines.extend([
        "",
        "## Notes",
        "",
        "- This gate aggregates existing scripts and reports only.",
        "- It does not enable query rewrite, summary, weighted rerank, or expanded search.",
        "- It does not rebuild vector indexes, clear Chroma, delete data, or write raw command output.",
    ])
    return "\n".join(lines)


def escape_md(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def collect_forbidden_terms() -> list[str]:
    terms: list[str] = []
    product_queries = DATA_EVAL_DIR / "product_eval_queries.jsonl"
    if product_queries.exists():
        for line in product_queries.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            value = str(row.get("query_text") or "").strip()
            if len(value) >= 8:
                terms.append(value)

    for path in (
        WEB_ROOT / "scripts" / "day3-7.1-e2e-smoke.mjs",
        API_ROOT / "scripts" / "day3_7_3_performance_smoke.py",
        API_ROOT / "scripts" / "day3_7_5_rollback_drill.py",
    ):
        if path.exists():
            text = path.read_text(encoding="utf-8", errors="replace")
            for value in re.findall(r'"([^"\n]{8,})"', text):
                if contains_cjk(value):
                    terms.append(value)
    unique: list[str] = []
    for term in terms:
        if term not in unique:
            unique.append(term)
    return unique


def contains_cjk(value: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in value)


def privacy_scan_gate(
    context: dict[str, Any],
    gates: list[GateItem],
    *,
    draft_json: str,
    draft_md: str,
) -> GateItem:
    started = perf_counter()
    command = "internal privacy scan"
    scan = scan_inline_and_files(
        context,
        inline_payloads={
            "draft_gate_json": draft_json,
            "draft_gate_markdown": draft_md,
        },
        paths=privacy_scan_paths(context, gates),
    )
    duration_ms = int((perf_counter() - started) * 1000)
    evidence_path = DOCS_DIR / f"{REPORT_PREFIX}-privacy-scan-{context['timestamp']}.json"
    evidence = {
        **scan,
        "forbidden_term_count": len(context.get("forbidden_terms") or []),
        "scanned_paths": [relative(path) for path in scan.get("scanned_paths", [])],
    }
    evidence_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    context["generated_evidence_paths"].append(str(evidence_path))
    passed = scan["status"] == "passed"
    return GateItem(
        name="privacy",
        status="passed" if passed else "failed",
        command=command,
        duration_ms=duration_ms,
        summary=(
            "Gate and latest recognizable reports do not contain collected raw query/case terms or forbidden raw fields."
            if passed
            else "Privacy scan found forbidden raw content or raw fields."
        ),
        evidence_path=str(evidence_path),
        failure_reason=None if passed else "privacy_scan_failed",
        details={
            "scanned_file_count": len(scan.get("scanned_paths", [])),
            "violation_count": len(scan.get("violations", [])),
        },
    )


def privacy_scan_paths(context: dict[str, Any], gates: list[GateItem]) -> list[Path]:
    paths: list[Path] = []
    for path in context.get("generated_evidence_paths") or []:
        paths.append(Path(path))
    for gate in gates:
        if gate.evidence_path:
            paths.append(Path(gate.evidence_path))
        bad_cases = gate.details.get("bad_cases_path") if gate.details else None
        if bad_cases:
            paths.append(Path(bad_cases))

    for pattern in (
        "m1.1-r3-performance-smoke*.json",
        "day3-7.3-performance-smoke*.json",
        f"{REPORT_PREFIX}-performance-smoke*.json",
        f"{REPORT_PREFIX}-day3-real-e2e*.json",
        f"{REPORT_PREFIX}-rerank-eval*.json",
        f"{REPORT_PREFIX}-product-eval*.json",
        f"{REPORT_PREFIX}-product-bad-cases*.json",
        f"{REPORT_PREFIX}-eval-preflight*.json",
    ):
        paths.extend(latest_matches(DOCS_DIR, pattern, limit=2))
    for pattern in (
        "product_eval_report_*.json",
        "bad_cases_product_eval_*.json",
        "eval_corpus_preflight*.json",
        "day3_rerank_eval*.json",
    ):
        paths.extend(latest_matches(DATA_EVAL_DIR, pattern, limit=2))

    unique: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        try:
            resolved = str(path.resolve())
        except OSError:
            continue
        if resolved not in seen and Path(resolved).exists():
            unique.append(Path(resolved))
            seen.add(resolved)
    return unique


def latest_matches(root: Path, pattern: str, *, limit: int) -> list[Path]:
    if not root.exists():
        return []
    matches = sorted(root.glob(pattern), key=lambda path: path.stat().st_mtime, reverse=True)
    return matches[:limit]


def scan_inline_and_files(
    context: dict[str, Any],
    *,
    inline_payloads: dict[str, str],
    paths: list[Path],
) -> dict[str, Any]:
    violations: list[dict[str, Any]] = []
    terms = context.get("forbidden_terms") or []

    for label, text in inline_payloads.items():
        violations.extend(scan_text(label, text, terms))
        violations.extend(scan_json_keys(label, text))

    scanned_paths: list[str] = []
    for path in paths:
        if not path.exists() or not path.is_file():
            continue
        scanned_paths.append(str(path))
        text = path.read_text(encoding="utf-8", errors="replace")
        violations.extend(scan_text(str(path), text, terms))
        violations.extend(scan_json_keys(str(path), text))

    return {
        "status": "passed" if not violations else "failed",
        "scanned_paths": scanned_paths,
        "violations": violations[:50],
    }


def scan_text(label: str, text: str, terms: list[str]) -> list[dict[str, Any]]:
    violations: list[dict[str, Any]] = []
    for index, term in enumerate(terms):
        if term and term in text:
            violations.append(
                {
                    "target": label,
                    "type": "raw_term_match",
                    "term_index": index,
                    "term_sha256": sha256_text(term),
                }
            )
    return violations


def scan_json_keys(label: str, text: str) -> list[dict[str, Any]]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return []
    violations: list[dict[str, Any]] = []

    def walk(value: Any, path: str) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                key_label = str(key)
                next_path = f"{path}.{key_label}" if path else key_label
                if key_label.lower() in FORBIDDEN_JSON_KEYS:
                    violations.append(
                        {
                            "target": label,
                            "type": "forbidden_json_key",
                            "path": next_path,
                        }
                    )
                walk(child, next_path)
        elif isinstance(value, list):
            for i, child in enumerate(value):
                walk(child, f"{path}[{i}]")

    walk(payload, "")
    return violations


if __name__ == "__main__":
    raise SystemExit(main())
