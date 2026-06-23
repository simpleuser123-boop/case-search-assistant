from __future__ import annotations

import json
from pathlib import Path

from app.core.config import Settings

PROJECT_ROOT = Path(__file__).resolve().parents[3]
CONTRACT_PATH = PROJECT_ROOT / "docs" / "development" / "m2-entry-contract-20260612-100850.json"
ENV_EXAMPLE_PATH = PROJECT_ROOT / ".env.example"


def _contract() -> dict:
    return json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))


def _env_example_values() -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in ENV_EXAMPLE_PATH.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def test_m2_entry_contract_freezes_m1_3x_robust_go_evidence():
    payload = _contract()

    assert payload["evidence"]["selected_candidate_id"] == (
        "m1_3x_legal_score_shape_router_v2_candidate"
    )
    assert payload["evidence"]["robust_status"] == "ROBUST_GO"
    assert payload["entry_register"]["current_core_metrics"] == {
        "top10_hit_rate": 0.72,
        "baseline_top10_hit_rate": 0.48,
        "ndcg_at_10": 0.1954,
        "baseline_ndcg_at_10": 0.134,
        "precision_at_5": 0.056,
        "baseline_precision_at_5": 0.032,
        "before_vs_after_regressed_count": 0,
        "after_vs_baseline_regressed_count": 0,
        "metric_regression_count": 0,
        "recall_miss_count": 7,
        "evaluated_query_count": 25,
        "repeat_count": 3,
        "repeat_consistent": True,
    }
    assert payload["go_no_go"]["status"] == "GO"
    assert payload["go_no_go"]["allows_entering_m2_2"] is True
    assert payload["go_no_go"]["allows_entering_m2_3_to_m2_8"] is False


def test_m2_entry_contract_keeps_rerank_and_expanded_search_defaults_closed():
    payload = _contract()
    env_values = _env_example_values()

    assert Settings.model_fields["ENABLE_WEIGHTED_RERANK"].default is False
    assert Settings.model_fields["ENABLE_EXPANDED_SEARCH"].default is False
    assert env_values["ENABLE_WEIGHTED_RERANK"] == "false"
    assert env_values["ENABLE_EXPANDED_SEARCH"] == "false"
    assert env_values["VITE_ENABLE_EXPANDED_SEARCH"] == "false"
    assert payload["feature_flag_strategy"]["ENABLE_WEIGHTED_RERANK"]["m2_1_decision"] == (
        "KEEP_FALSE"
    )
    assert payload["feature_flag_strategy"]["ENABLE_EXPANDED_SEARCH"]["m2_1_decision"] == (
        "KEEP_FALSE_UNTIL_GATED"
    )
    assert payload["go_no_go"]["allows_weighted_rerank_default_enablement"] is False
    assert payload["go_no_go"]["allows_expanded_search_default_enablement"] is False


def test_m2_entry_contract_defines_required_trusted_retrieval_fields():
    payload = _contract()

    assert set(payload["trusted_retrieval_contract"]) == {
        "source_anchors",
        "coverage",
        "confidence_level",
        "low_confidence_candidates",
        "expanded_search",
        "feedback_event",
        "risk_hint",
    }
    assert "case_id" in payload["trusted_retrieval_contract"]["source_anchors"]["api_fields"]
    assert "source_chunk_id" in payload["trusted_retrieval_contract"]["source_anchors"]["api_fields"]
    assert "query_hash" in payload["trusted_retrieval_contract"]["feedback_event"]["api_fields"]
    assert payload["trusted_retrieval_contract"]["feedback_event"]["affects_current_ranking"] is False
    assert payload["trusted_retrieval_contract"]["risk_hint"]["source_required_before_display"] is True


def test_m2_entry_contract_reports_and_events_do_not_allow_body_storage():
    payload = _contract()

    assert payload["body_boundary_policy"]["this_artifact_contains_body_text"] is False
    assert payload["body_boundary_policy"]["logs_events_reports_can_store_display_snippets"] is False
    assert payload["body_boundary_policy"]["development_artifacts_can_store_display_snippets"] is False

    for field_contract in payload["trusted_retrieval_contract"].values():
        assert field_contract["logs_events_reports_may_store_body"] is False

    allowed_fields = set(payload["body_boundary_policy"]["allowed_report_json_fields"])
    assert allowed_fields <= {
        "sanitized_id",
        "hash",
        "count",
        "rank",
        "score",
        "score_band",
        "status_code",
        "reason_code",
        "feature_flag_state",
        "metric_summary",
    }
