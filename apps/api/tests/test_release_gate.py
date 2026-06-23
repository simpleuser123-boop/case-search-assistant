from __future__ import annotations

from pathlib import Path

from scripts import release_gate
from scripts.release_gate import GateItem, compute_decisions, scan_inline_and_files


def _gate(name: str, status: str = "passed", details: dict | None = None) -> GateItem:
    return GateItem(
        name=name,
        status=status,
        command=f"cmd:{name}",
        duration_ms=1,
        summary=f"{name} {status}",
        failure_reason=None if status == "passed" else f"{name}_not_passed",
        details=details or {},
    )


def test_decisions_allow_basic_search_but_block_new_rerank_without_positive_eval():
    gates = [
        _gate("default_feature_flags"),
        _gate("backend_tests"),
        _gate("frontend_tests"),
        _gate("frontend_build"),
        _gate("day3_real_e2e"),
        _gate("runtime_preflight"),
        _gate("performance"),
        _gate("rollback"),
        _gate("privacy"),
        _gate("eval_corpus_preflight"),
        _gate(
            "product_eval",
            details={"gray_candidate": {"eligible": False, "reason": "candidate threshold not met"}},
        ),
        _gate("rerank_eval", status="blocked"),
        _gate("db_smoke", status="failed"),
    ]

    decisions = compute_decisions(gates)

    assert decisions["internal_basic_search"]["decision"] == "GO"
    assert decisions["external_gray"]["decision"] == "GO"
    assert decisions["new_rerank"]["decision"] == "NO_GO"
    assert decisions["new_rerank"]["reason"] == "candidate threshold not met"
    assert decisions["db_note"]["decision"] == "CONDITIONAL"


def test_decisions_do_not_treat_blocked_runtime_as_go():
    gates = [
        _gate("default_feature_flags"),
        _gate("backend_tests"),
        _gate("frontend_tests"),
        _gate("frontend_build"),
        _gate("day3_real_e2e"),
        _gate("runtime_preflight", status="blocked"),
        _gate("performance"),
        _gate("rollback"),
        _gate("privacy"),
        _gate("eval_corpus_preflight"),
        _gate("product_eval"),
    ]

    decisions = compute_decisions(gates)

    assert decisions["internal_basic_search"]["decision"] == "NO_GO"
    assert "runtime_preflight" in decisions["internal_basic_search"]["blocking_gates"]
    assert decisions["external_gray"]["decision"] == "NO_GO"


def test_decisions_require_m13_hard_gate_even_when_aggregate_eval_is_positive():
    gates = [
        _gate("default_feature_flags"),
        _gate("backend_tests"),
        _gate("frontend_tests"),
        _gate("frontend_build"),
        _gate("day3_real_e2e"),
        _gate("runtime_preflight"),
        _gate("performance"),
        _gate("rollback"),
        _gate("privacy"),
        _gate("eval_corpus_preflight"),
        _gate(
            "product_eval",
            details={
                "gray_candidate": {
                    "eligible": True,
                    "grayCandidateHardGatePassed": False,
                    "weightedRerankGrayCandidate": False,
                    "hardGateFailedReasons": ["BEFORE_VS_AFTER_REGRESSED_GT_0"],
                    "reason": "M1.3 hard gate failed.",
                }
            },
        ),
        _gate("rerank_eval", status="blocked"),
        _gate("db_smoke"),
    ]

    decisions = compute_decisions(gates)

    assert decisions["new_rerank"]["decision"] == "NO_GO"
    assert decisions["new_rerank"]["eval_gray_candidate_eligible"] is False
    assert decisions["new_rerank"]["m13_hard_gate_passed"] is False


def test_privacy_scan_flags_raw_terms_and_forbidden_json_keys():
    context = {"forbidden_terms": ["raw query value 12345"]}

    scan = scan_inline_and_files(
        context,
        inline_payloads={
            "gate": '{"ok": true, "payload": "raw query value 12345", "query_text": "x"}'
        },
        paths=[],
    )

    assert scan["status"] == "failed"
    assert {item["type"] for item in scan["violations"]} == {
        "raw_term_match",
        "forbidden_json_key",
    }


def test_safe_env_overrides_inherited_env_with_project_env(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("DEEPSEEK_BASE_URL=https://api.deepseek.com\n", encoding="utf-8")
    monkeypatch.setattr(release_gate, "PROJECT_ROOT", Path(tmp_path))
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://bobdong.cn/")

    env = release_gate.safe_env()

    assert env["DEEPSEEK_BASE_URL"] == "https://api.deepseek.com"
    assert env["ENABLE_QUERY_REWRITE"] == "false"
    assert env["ENABLE_WEIGHTED_RERANK"] == "false"
    assert env["ENABLE_SUMMARY"] == "false"
    assert env["ENABLE_EXPANDED_SEARCH"] == "false"
