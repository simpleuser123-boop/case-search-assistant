from __future__ import annotations

import json
from pathlib import Path

from app.core.config import Settings

PROJECT_ROOT = Path(__file__).resolve().parents[3]
CONTRACT_JSON = (
    PROJECT_ROOT
    / "docs"
    / "development"
    / "m4-workflow-entry-contract-20260613-090354.json"
)
CONTRACT_MD = (
    PROJECT_ROOT
    / "docs"
    / "development"
    / "m4-workflow-entry-contract-20260613-090354.md"
)
ENV_EXAMPLE_PATH = PROJECT_ROOT / ".env.example"

M4_FLAGS = (
    "ENABLE_SEARCH_HISTORY",
    "ENABLE_CASE_FAVORITE",
    "ENABLE_CASE_LIST",
    "ENABLE_LIST_EXPORT",
    "ENABLE_REPORT_TEMPLATE",
    "ENABLE_TEAM_REUSE",
)


def _contract() -> dict:
    return json.loads(CONTRACT_JSON.read_text(encoding="utf-8"))


def _env_example_values() -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in ENV_EXAMPLE_PATH.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def test_m4_contract_artifacts_exist_and_parse():
    assert CONTRACT_JSON.exists()
    assert CONTRACT_MD.exists()
    payload = _contract()
    assert payload["step"] == "M4-1"
    assert payload["artifact"] == "m4-workflow-entry-contract"


def test_m4_entry_register_freezes_m3_8_go_evidence():
    payload = _contract()
    register = payload["entry_register"]

    assert register["m3_8_conclusions"] == {
        "base_search_available": "GO",
        "m3_reading_efficiency_complete": "GO",
        "m4_entry": "GO",
        "rollback_required": False,
    }
    assert register["all_m3_steps_go"] is True
    steps = {s["step"]: s["status"] for s in register["m3_steps"]}
    assert steps == {
        "M3-1": "GO",
        "M3-2": "GO",
        "M3-3": "GO",
        "M3-4": "GO",
        "M3-5": "GO",
        "M3-6": "GO",
        "M3-7": "GO",
    }
    inherited = register["inherited_gates_from_m2_m3"]
    assert inherited["source_anchor_minimum_fields"] == ["case_id", "source_chunk_id"]
    assert inherited["user_side_raw_text_not_persisted"] is True
    assert inherited["main_sorting_unchanged"] is True
    assert inherited["no_win_loss_probability_or_certain_legal_conclusion"] is True


def test_m4_contract_keeps_weighted_rerank_default_false():
    payload = _contract()
    env_values = _env_example_values()

    assert Settings.model_fields["ENABLE_WEIGHTED_RERANK"].default is False
    assert env_values["ENABLE_WEIGHTED_RERANK"] == "false"
    evidence = payload["entry_register"]["weighted_rerank_evidence"]
    assert evidence["config_field_default"] is False
    assert evidence["env_example_value"] == "false"
    assert evidence["m4_1_decision"] == "KEEP_FALSE"
    assert payload["go_no_go"]["allows_weighted_rerank_default_enablement"] is False


def test_m4_new_feature_flags_declared_and_default_false():
    payload = _contract()
    env_values = _env_example_values()
    strategy = payload["feature_flag_strategy"]

    for flag in M4_FLAGS:
        # config.py declares the flag and defaults it to False
        assert flag in Settings.model_fields, f"{flag} missing from Settings"
        assert Settings.model_fields[flag].default is False, f"{flag} default not False"
        # .env.example declares the flag as false
        assert env_values[flag] == "false", f"{flag} env_example not false"
        # contract records safe-default decision
        assert strategy[flag]["config_default"] is False
        assert strategy[flag]["env_example_default"] is False
        assert strategy[flag]["m4_1_decision"] == "DECLARE_FALSE_SAFE_DEFAULT"

    assert strategy["m4_flags_change_standard_search_default"] is False
    assert strategy["disabled_returns_to_m3_end_state"] is True
    assert payload["go_no_go"]["allows_any_m4_flag_default_enablement"] is False


def test_m4_persistable_field_whitelist_is_frozen_and_disjoint():
    payload = _contract()
    whitelist = payload["persistable_field_whitelist"]
    allowed = set(whitelist["allowed_persistable_fields"])
    forbidden = set(whitelist["forbidden_persistable_fields"])

    # required allowed metadata fields are present
    for field in (
        "case_id",
        "case_number",
        "court",
        "trial_level",
        "case_cause",
        "judgment_date",
        "source_anchors",
        "note",
        "tag",
        "label",
        "created_at",
        "status",
        "reason_code",
    ):
        assert field in allowed, f"{field} must be persistable"

    # body-type content must be forbidden
    for field in (
        "raw_query",
        "case_fact_body",
        "candidate_body",
        "chunk_body",
        "judgment_long_text",
        "summary_body_content",
        "user_free_long_text",
    ):
        assert field in forbidden, f"{field} must be forbidden"

    # allowed and forbidden sets must not overlap
    assert allowed.isdisjoint(forbidden)
    # user raw case / draft body never persisted on server
    assert whitelist["user_raw_case_persisted_on_server"] is False
    assert whitelist["draft_body_persisted_on_server"] is False


def test_m4_workflow_contract_fields_declare_no_runtime_or_body_impact():
    payload = _contract()
    fields = payload["workflow_contract_fields"]

    assert set(fields) == {
        "search_history",
        "search_draft",
        "case_favorite",
        "case_list",
        "case_list_export",
        "report_template",
        "team_reuse_capability",
    }
    for name, field in fields.items():
        assert field["implemented_in_m4_1"] is False, f"{name} must not be implemented in M4-1"
        assert field["affects_ranking"] is False, f"{name} must not affect ranking"
        assert field["logs_events_reports_may_store_body"] is False, f"{name} body leak"

    # draft body is local-only, never persisted on server
    assert fields["search_draft"]["server_persistable_fields"] == []
    assert fields["search_draft"]["storage_location"] == "browser_local_only_clearable"
    # favorite / list cannot persist body
    assert "case_body" in fields["case_favorite"]["forbidden_persistable_fields"]
    assert "chunk_body" in fields["case_list"]["forbidden_persistable_fields"]
    # team reuse stays an assessment, default private
    assert fields["team_reuse_capability"]["implements_real_multiuser_in_m4"] is False
    assert fields["team_reuse_capability"]["default_cross_user_visibility"] is False


def test_m4_anchor_inheritance_requires_case_id_and_source_chunk_id():
    payload = _contract()
    rules = payload["anchor_inheritance_rules"]

    assert rules["required_anchor_fields"] == ["case_id", "source_chunk_id"]
    assert rules["no_anchor_no_delivery"] is True
    assert rules["unanchored_content_enters_deliverable"] is False
    assert rules["fabricates_source_chunk_id"] is False
    assert set(rules["applies_to"]) == {"case_list", "case_list_export", "report_template"}


def test_m4_body_boundary_policy_blocks_body_in_artifacts():
    payload = _contract()
    policy = payload["body_boundary_policy"]

    assert policy["this_artifact_contains_body_text"] is False
    assert policy["logs_events_reports_exports_can_store_body"] is False
    assert policy["development_artifacts_can_store_body"] is False

    allowed = set(policy["allowed_report_json_fields"])
    assert allowed <= {
        "field_name",
        "count",
        "status",
        "reason_code",
        "feature_flag_state",
        "metadata",
        "source_anchor",
        "user_filled_short_field",
        "metric_summary",
        "test_result",
        "conclusion",
        "sanitized_id",
        "hash",
        "timestamp",
    }
    for forbidden in (
        "original_query_body",
        "case_fact_body",
        "candidate_body",
        "chunk_body",
        "judgment_text_body",
        "user_free_long_text",
    ):
        assert forbidden in policy["forbidden_body_content_types"]


def test_m4_contract_does_not_implement_m4_2_to_m4_7():
    payload = _contract()
    scope = payload["scope"]
    assert scope["implements_m4_2_to_m4_7"] is False
    assert scope["changes_online_sorting"] is False
    assert scope["changes_source_selection"] is False
    assert scope["changes_rerank_defaults"] is False
    assert scope["changes_qrels_or_labels"] is False
    assert scope["writes_business_persistence_behavior"] is False

    forbidden = set(payload["forbidden_behaviors"])
    for behavior in (
        "PERSIST_RAW_QUERY_OR_CASE_BODY_TO_SERVER",
        "PERSIST_FIELD_NOT_IN_WHITELIST",
        "ENABLE_WEIGHTED_RERANK_BY_DEFAULT",
        "ENABLE_ANY_M4_FLAG_BY_DEFAULT",
        "CHANGE_ONLINE_RANKING_OR_SOURCE_SELECTION",
        "PROMISE_EXHAUSTIVE_SEARCH",
        "OUTPUT_WIN_LOSS_PROBABILITY_OR_CERTAIN_LEGAL_CONCLUSION",
        "AUTO_DRAFT_LEGAL_DOCUMENT_IN_REPORT",
    ):
        assert behavior in forbidden


def test_m4_go_no_go_status_is_go():
    payload = _contract()
    gate = payload["go_no_go"]
    assert gate["status"] == "GO"
    assert gate["allows_entering_m4_2"] is True
    assert gate["m3_8_entry_evidence_complete"] is True
    assert gate["persistence_whitelist_defined"] is True
    assert gate["anchor_inheritance_defined"] is True
    assert gate["body_boundary_defined"] is True
    assert gate["changes_basic_search_default_behavior"] is False
    assert gate["body_leakage_in_artifact"] is False
    assert gate["failed_gates"] == []
    assert gate["next_allowed_title"] == "类案检索助手 M4-2 检索历史与草稿恢复"
