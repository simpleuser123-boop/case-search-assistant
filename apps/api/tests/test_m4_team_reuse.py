from __future__ import annotations

import json
from pathlib import Path

from app.core.config import Settings

PROJECT_ROOT = Path(__file__).resolve().parents[3]
ASSESSMENT_JSON = (
    PROJECT_ROOT
    / "docs"
    / "development"
    / "m4-team-reuse-assessment-20260614-015309.json"
)
ASSESSMENT_MD = (
    PROJECT_ROOT
    / "docs"
    / "development"
    / "m4-team-reuse-assessment-20260614-015309.md"
)
ENV_EXAMPLE_PATH = PROJECT_ROOT / ".env.example"

READINESS_VOCAB = {"ready", "partially_ready", "not_ready"}


def _assessment() -> dict:
    return json.loads(ASSESSMENT_JSON.read_text(encoding="utf-8"))


def _env_example_values() -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in ENV_EXAMPLE_PATH.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def test_m4_team_reuse_artifacts_exist_and_parse():
    assert ASSESSMENT_JSON.exists()
    assert ASSESSMENT_MD.exists()
    payload = _assessment()
    assert payload["step"] == "M4-7"
    assert payload["artifact"] == "m4-team-reuse-assessment"


def test_m4_team_reuse_inherits_m4_1_to_m4_6_go():
    payload = _assessment()
    evidence = payload["entry_evidence"]
    assert evidence["m4_1_go"] is True
    assert evidence["m4_2_go"] is True
    assert evidence["m4_3_go"] is True
    assert evidence["m4_4_go"] is True
    assert evidence["m4_5_go"] is True
    assert evidence["m4_6_go"] is True
    assert evidence["all_prerequisites_go"] is True


def test_team_reuse_flag_default_false_in_config_and_env():
    payload = _assessment()
    env_values = _env_example_values()

    assert Settings.model_fields["ENABLE_TEAM_REUSE"].default is False
    assert env_values["ENABLE_TEAM_REUSE"] == "false"
    flag = payload["feature_flag"]
    assert flag["name"] == "ENABLE_TEAM_REUSE"
    assert flag["config_default"] is False
    assert flag["env_example_default"] is False
    assert flag["decision"] == "KEEP_FALSE_ASSESSMENT_ONLY"


def test_assessment_structure_uses_required_fields_and_readiness_vocab():
    payload = _assessment()
    structure = payload["assessment_structure"]
    assert structure["fields"] == [
        "capability",
        "readiness",
        "prerequisite",
        "data_structure_reserved",
        "risk_note",
    ]
    assert set(structure["readiness_vocab"]) == READINESS_VOCAB

    capabilities = payload["capabilities"]
    expected = {
        "account_system",
        "list_sharing",
        "permission_tiering",
        "team_workspace_isolation",
        "bulk_import",
    }
    assert {c["capability"] for c in capabilities} == expected
    for cap in capabilities:
        # every capability declares the full assessment structure
        for field in ("capability", "readiness", "prerequisite", "data_structure_reserved", "risk_note"):
            assert field in cap, f"{cap['capability']} missing {field}"
        assert cap["readiness"] in READINESS_VOCAB
        assert isinstance(cap["prerequisite"], list) and cap["prerequisite"]
        assert isinstance(cap["risk_note"], str) and cap["risk_note"]


def test_reserved_fields_default_private_and_sharing_disabled():
    payload = _assessment()
    reserved = payload["reserved_fields"]
    # declaration-only: not injected into live types or server schema
    assert reserved["declaration_only"] is True
    assert reserved["injected_into_live_types"] is False
    assert reserved["injected_into_server_schema"] is False

    slots = reserved["slots"]
    assert slots["owner"]["default"] == "private"
    assert slots["visibility"]["default"] == "private"
    assert slots["sharing"]["default"] == "disabled"
    assert slots["team_id"]["default"] is None
    assert slots["owner_user_id"]["default"] is None
    for slot in slots.values():
        assert slot["default_changes_existing_behavior"] is False

    # reserved defaults match the frozen M4-1 contract
    assert reserved["reserved_defaults_match_m4_1_contract"] == {
        "owner": "private",
        "visibility": "private",
        "sharing": "disabled",
    }


def test_scope_is_assessment_only_no_multiuser_implementation():
    payload = _assessment()
    scope = payload["scope"]
    assert scope["assessment_only"] is True
    assert scope["reserves_fields_declaration_only"] is True
    assert scope["implements_real_multiuser"] is False
    assert scope["implements_team_space"] is False
    assert scope["implements_role_permission"] is False
    assert scope["implements_bulk_import"] is False
    assert scope["implements_share_link"] is False
    assert scope["default_cross_user_visibility"] is False
    assert scope["changes_online_sorting"] is False
    assert scope["changes_source_selection"] is False
    assert scope["changes_rerank_defaults"] is False
    assert scope["changes_existing_m1_m2_m3_m4_default_behavior"] is False
    assert scope["backend_files_changed"] == 0
    assert scope["frontend_business_files_changed"] == 0


def test_current_persistence_baseline_is_frontend_only_no_server():
    payload = _assessment()
    baseline = payload["current_persistence_baseline"]
    assert baseline["backend_persistence_tables_exist"] is False
    assert baseline["backend_account_system_exists"] is False
    assert baseline["user_identity_concept_exists"] is False
    for key in (
        "search_history_draft",
        "case_favorite",
        "case_list",
        "case_list_export",
        "report_template",
    ):
        assert baseline[key]["server_persisted"] is False


def test_gate3_evidence_review_present():
    payload = _assessment()
    review = payload["gate3_evidence_review"]
    criteria = review["criteria"]
    assert len(criteria) == 3
    for item in criteria:
        assert item["status"] in READINESS_VOCAB
        assert item["note"]
    assert review["overall"] == "capability_ready_evidence_pending"


def test_refactor_points_carry_privacy_risk():
    payload = _assessment()
    points = payload["single_user_to_team_refactor_points"]["refactor_points"]
    assert len(points) >= 5
    for point in points:
        assert point["from"]
        assert point["to"]
        assert point["privacy_risk"]


def test_privacy_gate_no_default_cross_user_visibility_or_body():
    payload = _assessment()
    gate = payload["privacy_gate"]
    assert gate["persist_layer_body_text"] is False
    assert gate["default_cross_user_visibility"] is False
    assert gate["default_sharing_enabled"] is False
    assert gate["reserved_owner_default_private"] is True
    assert gate["reserved_visibility_default_private"] is True
    assert gate["reserved_sharing_default_disabled"] is True
    assert gate["no_body_leak_in_artifact"] is True
    assert gate["this_artifact_contains_body_text"] is False


def test_not_doing_list_blocks_team_space_and_default_sharing():
    payload = _assessment()
    not_doing = payload["not_doing"]
    joined = "".join(not_doing)
    assert "不实现真实多用户权限" in joined
    assert "不实现共享链接" in joined
    assert "不实现团队空间" in joined
    assert "不实现批量导入" in joined
    assert "不默认开启跨用户可见性或共享" in joined
    assert "不改变 M1/M2/M3/M4 既有默认行为" in joined


def test_other_m4_flags_remain_default_false_no_regression():
    # M4-7 must not enable any prior flag by side effect
    for flag in (
        "ENABLE_WEIGHTED_RERANK",
        "ENABLE_SEARCH_HISTORY",
        "ENABLE_CASE_FAVORITE",
        "ENABLE_CASE_LIST",
        "ENABLE_LIST_EXPORT",
        "ENABLE_REPORT_TEMPLATE",
        "ENABLE_TEAM_REUSE",
    ):
        assert Settings.model_fields[flag].default is False, f"{flag} default not False"


def test_stop_loss_not_triggered():
    payload = _assessment()
    checks = payload["stop_loss_checks"]
    assert checks["implemented_multiuser_permission_or_sharing"] is False
    assert checks["default_enabled_cross_user_visibility"] is False
    assert checks["caused_existing_behavior_regression"] is False
    assert checks["triggered_no_go"] is False


def test_go_no_go_status_is_go():
    payload = _assessment()
    gate = payload["go_no_go"]
    assert gate["status"] == "GO"
    assert gate["allows_entering_m4_8"] is True
    assert gate["failed_gates"] == []
    assert gate["next_allowed_title"] == "类案检索助手 M4-8 验收与 M5 入口"
