from __future__ import annotations

import json
from pathlib import Path

from app.core.config import Settings

PROJECT_ROOT = Path(__file__).resolve().parents[3]
CONTRACT_JSON = (
    PROJECT_ROOT
    / "docs"
    / "development"
    / "m5-commercial-entry-contract-20260614-030248.json"
)
CONTRACT_MD = (
    PROJECT_ROOT
    / "docs"
    / "development"
    / "m5-commercial-entry-contract-20260614-030248.md"
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

M5_FLAGS = (
    "ENABLE_ACCOUNT_SYSTEM",
    "ENABLE_TEAM_WORKSPACE",
    "ENABLE_PERMISSION_TIERING",
    "ENABLE_TEAM_SHARING",
    "ENABLE_BULK_IMPORT",
    "ENABLE_TENDENCY_ANALYSIS",
    "ENABLE_BILLING",
)

# Credential markers that must NEVER appear as allowed/persistable values.
FORBIDDEN_CREDENTIAL_TOKENS = (
    "plaintext_password",
    "sso_oauth_token",
    "session_token",
    "card_number",
    "bank_account",
    "cvv",
    "payment_token_plaintext",
    "government_id_number",
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


def test_m5_contract_artifacts_exist_and_parse():
    assert CONTRACT_JSON.exists()
    assert CONTRACT_MD.exists()
    payload = _contract()
    assert payload["step"] == "M5-1"
    assert payload["artifact"] == "m5-commercial-entry-contract"


def test_m5_entry_register_freezes_m4_8_go_evidence():
    payload = _contract()
    register = payload["entry_register"]

    assert register["m4_8_conclusions"] == {
        "base_search_available": "GO",
        "m4_workflow_sedimentation_complete": "GO",
        "m5_entry": "GO",
        "rollback_required": False,
    }
    assert register["all_m4_8_artifacts_exist"] is True
    assert register["all_m4_steps_go"] is True
    steps = {s["step"]: s["status"] for s in register["m4_steps"]}
    assert steps == {
        "M4-1": "GO",
        "M4-2": "GO",
        "M4-3": "GO",
        "M4-4": "GO",
        "M4-5": "GO",
        "M4-6": "GO",
        "M4-7": "GO",
    }


def test_m5_entry_register_records_m4_7_not_ready_and_reserved_slots():
    payload = _contract()
    register = payload["entry_register"]

    not_ready = {c["capability"]: c for c in register["m4_7_not_ready_capabilities"]}
    assert set(not_ready) == {
        "account_system",
        "list_sharing",
        "permission_tiering",
        "team_workspace_isolation",
        "bulk_import",
    }
    for cap in not_ready.values():
        assert cap["readiness"] == "not_ready"

    reserved = set(register["m4_7_reserved_field_slots"])
    for slot in ("owner", "visibility", "shared_with_team_id", "team_id", "workspace_id"):
        assert slot in reserved, f"{slot} reserved slot missing"

    defaults = register["m4_7_reserved_defaults"]
    assert defaults["owner"] == "private"
    assert defaults["visibility"] == "private"
    assert defaults["sharing"] == "disabled"
    assert defaults["team_id"] is None
    assert defaults["owner_user_id"] is None


def test_m5_inherited_gates_from_m2_m3_m4():
    payload = _contract()
    inherited = payload["entry_register"]["inherited_gates_from_m2_m3_m4"]

    assert inherited["source_anchor_minimum_fields"] == ["case_id", "source_chunk_id"]
    assert inherited["user_side_raw_text_not_persisted_on_server"] is True
    assert inherited["persistence_layer_only_metadata_anchor_user_short_fields"] is True
    assert inherited["no_exhaustive_coverage_wording"] is True
    assert inherited["no_win_loss_probability_or_certain_legal_conclusion"] is True
    assert inherited["main_sorting_unchanged"] is True
    assert inherited["source_selection_unchanged"] is True
    assert inherited["rerank_default_unchanged"] is True
    assert inherited["qrels_label_relevance_offline_only"] is True
    assert inherited["no_query_id_or_case_id_runtime_special_case"] is True
    assert inherited["no_body_leak_in_persistence_export_report_log_snapshot"] is True


def test_m5_keeps_weighted_rerank_and_m4_flags_default_false():
    payload = _contract()
    env_values = _env_example_values()

    # weighted rerank
    assert Settings.model_fields["ENABLE_WEIGHTED_RERANK"].default is False
    assert env_values["ENABLE_WEIGHTED_RERANK"] == "false"
    we = payload["entry_register"]["weighted_rerank_evidence"]
    assert we["config_field_default"] is False
    assert we["env_example_value"] == "false"
    assert we["m5_1_decision"] == "KEEP_FALSE"

    # 6 M4 flags
    for flag in M4_FLAGS:
        assert Settings.model_fields[flag].default is False, f"{flag} default not False"
        assert env_values[flag] == "false", f"{flag} env_example not false"

    strategy = payload["feature_flag_strategy"]
    assert strategy["ENABLE_WEIGHTED_RERANK"]["m5_1_decision"] == "KEEP_FALSE"
    for flag in M4_FLAGS:
        assert strategy[flag]["config_default"] is False
        assert strategy[flag]["m5_1_decision"] == "KEEP_FALSE"


def test_m5_new_feature_flags_declared_and_default_false():
    payload = _contract()
    env_values = _env_example_values()
    strategy = payload["feature_flag_strategy"]

    for flag in M5_FLAGS:
        # config.py declares the flag and defaults it to False
        assert flag in Settings.model_fields, f"{flag} missing from Settings"
        assert Settings.model_fields[flag].default is False, f"{flag} default not False"
        # .env.example declares the flag as false
        assert env_values[flag] == "false", f"{flag} env_example not false"
        # contract records safe-default decision
        assert strategy[flag]["config_default"] is False
        assert strategy[flag]["env_example_default"] is False
        assert strategy[flag]["m5_1_decision"] == "DECLARE_FALSE_SAFE_DEFAULT"

    assert strategy["m5_flags_change_standard_search_default"] is False
    assert strategy["disabled_returns_to_m4_end_state"] is True
    new_caps = strategy["new_m5_capabilities"]
    assert new_caps["must_not_default_enable_cross_user_visibility"] is True
    assert new_caps["must_not_manage_plaintext_credentials"] is True
    assert payload["go_no_go"]["allows_any_m5_flag_default_enablement"] is False


def test_m5_server_multiuser_persistable_whitelist_inherits_m4_and_is_disjoint():
    payload = _contract()
    whitelist = payload["server_multiuser_persistable_field_whitelist"]
    allowed = set(whitelist["allowed_persistable_fields"])
    forbidden = set(whitelist["forbidden_persistable_fields"])

    # M4 whitelist fully inherited
    assert whitelist["inherits_m4_whitelist"] is True
    for field in whitelist["m4_allowed_fields"]:
        assert field in allowed, f"M4 field {field} must remain persistable"

    # required metadata / anchor / user short fields present
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

    # M5 new structured relation fields present
    for field in (
        "owner_user_id",
        "team_id",
        "workspace_id",
        "visibility",
        "role",
        "shared_with_team_id",
        "bulk_import_job_id",
        "billing_plan_id",
        "subscription_status",
    ):
        assert field in allowed, f"M5 structured relation {field} must be persistable"
        assert field in whitelist["m5_new_structured_relation_fields"]

    # body content forbidden (M4 ban fully in force)
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

    assert allowed.isdisjoint(forbidden)
    assert whitelist["user_raw_case_persisted_on_server"] is False
    assert whitelist["draft_body_persisted_on_server"] is False
    assert whitelist["m4_body_forbidden_rules_fully_in_force"] is True
    assert whitelist["whitelist_only_metadata_reference_structured_relation"] is True

    # No credential token may appear in the allowed (persistable) set.
    for token in FORBIDDEN_CREDENTIAL_TOKENS:
        assert token not in allowed, f"credential token {token} must not be persistable"


def test_m5_credential_security_redlines():
    payload = _contract()
    redlines = payload["credential_security_redlines"]

    assert redlines["password_stored_as_one_way_hash_only"] is True
    assert redlines["password_plaintext_storage_forbidden"] is True
    assert redlines["tool_autofills_credentials"] is False
    assert redlines["tool_manages_plaintext_credentials"] is False
    assert redlines["tool_stores_plaintext_credentials"] is False
    assert redlines["sensitive_input_done_by_user_or_platform_side"] is True
    assert redlines["payment_done_on_platform_or_third_party"] is True

    never = set(redlines["credentials_never_persisted_anywhere"])
    for token in (
        "plaintext_password",
        "sso_oauth_token",
        "session_token",
        "payment_card_number",
        "bank_account_number",
        "cvv",
        "government_id_number",
    ):
        assert token in never, f"{token} must be in never-persist list"

    targets = set(redlines["redline_targets"])
    for target in ("server_business_table", "log", "json_artifact", "report", "test_snapshot"):
        assert target in targets, f"{target} must be a redline target"


def test_m5_commercial_contract_fields_declare_no_runtime_or_body_impact():
    payload = _contract()
    fields = payload["commercial_contract_fields"]

    assert set(fields) == {
        "account",
        "team",
        "membership_role",
        "shared_object",
        "bulk_import_job",
        "tendency_analysis",
        "billing_plan_subscription",
    }
    for name, field in fields.items():
        assert field["implemented_in_m5_1"] is False, f"{name} must not be implemented in M5-1"
        assert field["affects_ranking"] is False, f"{name} must not affect ranking"
        assert field["logs_events_reports_may_store_body"] is False, f"{name} body leak"

    # account: credential handling
    cred = fields["account"]["credential_handling"]
    assert cred["password_storage"] == "one_way_hash_with_salt_only"
    assert cred["stores_plaintext_password"] is False
    assert cred["stores_sso_oauth_token"] is False
    assert cred["tool_autofills_or_manages_credentials"] is False

    # shared_object: default private, anchors, no body sync
    shared = fields["shared_object"]
    assert shared["default_visibility"] == "private"
    assert shared["default_cross_user_visibility"] is False
    assert shared["sharing_requires_explicit_action"] is True
    assert shared["anchored_ai_content_requires_case_id_and_source_chunk_id"] is True
    assert shared["syncs_body_or_raw_case_to_server"] is False

    # bulk_import: no body, no fabricated anchors, default private
    bulk = fields["bulk_import_job"]
    assert bulk["imports_body_content"] is False
    assert bulk["fabricates_source_chunk_id"] is False
    assert bulk["rejects_or_degrades_items_missing_anchor"] is True
    assert bulk["imported_object_default_visibility"] == "private"

    # tendency analysis: gate before display, no prediction
    tend = fields["tendency_analysis"]
    assert tend["requires_data_quality_gate_pass_before_display"] is True
    assert tend["predicts_individual_case_outcome"] is False
    assert tend["outputs_win_loss_probability"] is False
    assert tend["outputs_certain_legal_conclusion"] is False
    assert tend["displays_case_body"] is False

    # billing: no credentials
    pay = fields["billing_plan_subscription"]["payment_handling"]
    assert pay["tool_autofills_payment_form"] is False
    assert pay["tool_manages_or_stores_plaintext_credentials"] is False
    assert pay["stores_only_masked_receipt_ref"] is True
    for token in ("card_number", "bank_account", "cvv", "payment_token_plaintext", "government_id_number"):
        assert token in fields["billing_plan_subscription"]["forbidden_persistable_fields"]


def test_m5_anchor_inheritance_requires_case_id_and_source_chunk_id():
    payload = _contract()
    rules = payload["anchor_inheritance_rules"]

    assert rules["required_anchor_fields"] == ["case_id", "source_chunk_id"]
    assert rules["no_anchor_no_delivery"] is True
    assert rules["unanchored_content_enters_deliverable"] is False
    assert rules["fabricates_source_chunk_id"] is False
    assert set(rules["applies_to"]) == {
        "shared_object",
        "bulk_import_job",
        "report_template",
        "tendency_analysis",
    }


def test_m5_body_boundary_and_forbidden_fields():
    payload = _contract()
    policy = payload["body_boundary_and_forbidden_fields"]

    assert policy["this_artifact_contains_body_text"] is False
    assert policy["this_artifact_contains_plaintext_credentials"] is False
    assert policy["logs_events_reports_exports_can_store_body"] is False
    assert policy["logs_events_reports_exports_can_store_credentials"] is False
    assert policy["development_artifacts_can_store_body"] is False
    assert policy["development_artifacts_can_store_credentials"] is False

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
        "structured_relation_field",
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
    for cred in (
        "plaintext_password",
        "sso_oauth_token",
        "card_number",
        "bank_account",
        "cvv",
        "government_id_number",
    ):
        assert cred in policy["forbidden_credential_types"]


def test_m5_scope_does_not_implement_m5_2_to_m5_10():
    payload = _contract()
    scope = payload["scope"]
    assert scope["is_contract_and_scaffold_only"] is True
    assert scope["implements_m5_2_to_m5_10"] is False
    assert scope["introduces_server_multiuser_state"] is False
    assert scope["changes_online_sorting"] is False
    assert scope["changes_source_selection"] is False
    assert scope["changes_rerank_defaults"] is False
    assert scope["changes_qrels_or_labels"] is False
    assert scope["writes_business_persistence_behavior"] is False
    assert scope["manages_or_autofills_credentials"] is False
    assert scope["backend_business_files_changed"] == 0
    assert scope["frontend_business_files_changed"] == 0

    forbidden = set(payload["forbidden_behaviors"])
    for behavior in (
        "IMPLEMENT_M5_2_TO_M5_10_CAPABILITY_IN_M5_1",
        "PERSIST_RAW_QUERY_OR_CASE_BODY_TO_SERVER",
        "PERSIST_FIELD_NOT_IN_WHITELIST",
        "STORE_PLAINTEXT_PASSWORD",
        "STORE_SSO_OAUTH_OR_SESSION_TOKEN_IN_TABLE_LOG_REPORT",
        "STORE_PAYMENT_CARD_BANK_CVV_OR_GOVERNMENT_ID",
        "TOOL_AUTOFILL_OR_MANAGE_PLAINTEXT_CREDENTIALS",
        "DEFAULT_ENABLE_CROSS_USER_VISIBILITY",
        "DEFAULT_NON_PRIVATE_SHARING",
        "ENABLE_ANY_M5_FLAG_BY_DEFAULT",
        "CHANGE_ONLINE_RANKING_OR_SOURCE_SELECTION",
        "PROMISE_EXHAUSTIVE_SEARCH",
        "OUTPUT_WIN_LOSS_PROBABILITY_OR_CERTAIN_LEGAL_CONCLUSION",
        "PREDICT_INDIVIDUAL_CASE_OUTCOME_IN_TENDENCY_ANALYSIS",
        "DISPLAY_TENDENCY_ANALYSIS_BEFORE_DATA_GATE_PASS",
    ):
        assert behavior in forbidden, f"{behavior} missing from forbidden_behaviors"


def test_m5_go_no_go_status_is_go():
    payload = _contract()
    gate = payload["go_no_go"]
    assert gate["status"] == "GO"
    assert gate["allows_entering_m5_2"] is True
    assert gate["allows_weighted_rerank_default_enablement"] is False
    assert gate["allows_any_m4_flag_default_enablement"] is False
    assert gate["allows_any_m5_flag_default_enablement"] is False
    assert gate["m4_8_entry_evidence_complete"] is True
    assert gate["server_multiuser_persistence_whitelist_defined"] is True
    assert gate["credential_security_redlines_defined"] is True
    assert gate["anchor_inheritance_defined"] is True
    assert gate["body_boundary_and_forbidden_fields_defined"] is True
    assert gate["changes_basic_search_default_behavior"] is False
    assert gate["body_leakage_in_artifact"] is False
    assert gate["plaintext_credential_in_artifact"] is False
    assert gate["failed_gates"] == []
    assert gate["next_allowed_title"] == "类案检索助手 M5-2 账号体系与认证骨架"


def test_m5_stop_loss_not_triggered():
    payload = _contract()
    checks = payload["stop_loss_checks"]
    assert checks["m4_8_artifacts_missing"] is False
    assert checks["m4_conclusion_no_longer_go"] is False
    assert checks["any_default_flag_enabled"] is False
    assert checks["whitelist_cannot_guarantee_metadata_reference_relation_only"] is False
    assert checks["credential_redline_cannot_guarantee_no_plaintext_management"] is False
    assert checks["any_triggered"] is False
