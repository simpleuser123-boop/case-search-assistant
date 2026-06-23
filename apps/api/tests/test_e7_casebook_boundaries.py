"""E7-5 casebook boundary guards.

Static AST scans, importlib identity checks, and focused runtime assertions
for the E7 casebook package. The file adds guard coverage only; it does not
change business behavior.
"""
from __future__ import annotations

import ast
import importlib
import json
from pathlib import Path

import pytest

API_ROOT = Path(__file__).resolve().parents[1]
APP_DIR = API_ROOT / "app"
REPO_ROOT = Path(__file__).resolve().parents[3]
CASEBOOK_DIR = APP_DIR / "casebook"
MAIN_PY = APP_DIR / "main.py"
CONFIG_PY = APP_DIR / "core" / "config.py"
WEB_CASEBOOK_PAGE = REPO_ROOT / "apps" / "web" / "src" / "pages" / "CasebookPage.tsx"
WEB_CASEBOOK_EXPORT = REPO_ROOT / "apps" / "web" / "src" / "lib" / "casebookExport.ts"

PRODUCT_PACKAGES = ("intake", "statute", "drafting", "casebook")
ALLOWED_PRODUCT_PACKAGES = {"intake", "statute", "drafting", "casebook"}
ALLOWED_KERNEL_SURFACES = ("app.kernel", "app.kernel.guardrails", "app.kernel.identity")
FORBIDDEN_RETRIEVAL_PREFIXES = (
    "app.retrieval",
    "app.rerank",
    "app.summary",
    "app.query_processing",
    "app.kernel.rag.retrieval",
    "app.kernel.rag.rerank",
    "app.kernel.rag.summary",
    "app.kernel.rag.query_processing",
)

FORBIDDEN_CASE_BODY_TOKENS = (
    "chunk_text",
    "judgment_text",
    "judgment_full_text",
    "summary_text",
    "highlight_text",
    "matched_text",
    "holding_summary",
    "case_body",
    "document_text",
    "raw_case",
    "raw_query",
)
FORBIDDEN_DRAFT_BODY_TOKENS = (
    "draft_body",
    "draft_content",
    "draft_text",
    "generated_text",
    "opinion_text",
    "paragraph_body",
    "paragraph_text",
    "conclusion_text",
)
FORBIDDEN_OUTCOME_TOKENS = (
    "case_summary_text",
    "case_summary",
    "summary_conclusion",
    "win_probability",
    "winning_probability",
    "outcome_prediction",
    "predicted_outcome",
    "verdict",
)
FORBIDDEN_PII_TOKENS = (
    "id_card",
    "id_card_no",
    "id_number",
    "passport_no",
    "phone_no",
    "phone_number",
    "mobile_no",
    "email_address",
    "bank_card",
    "bank_account",
    "home_address",
    "residential_address",
    "party_name",
    "defendant_name",
    "plaintiff_name",
    "real_name",
)
FORBIDDEN_CREDENTIAL_TOKENS = (
    "password",
    "access_token",
    "refresh_token",
    "api_key",
    "secret_key",
)
FORBIDDEN_ABSOLUTE_PHRASES = (
    "已查全",
    "保证无遗漏",
    "必然胜诉",
    "稳赢",
    "绝对覆盖",
)
TEXT_GENERATION_MARKERS = (
    "openai",
    "deepseek",
    "chat.completions",
    "llm.generate",
    "generate_text",
    "generate_draft",
    "draft_paragraph",
    "summarize(",
    "completion(",
)
BACKFEED_MARKERS = (
    "feedback",
    "rank_feedback",
    "ranking_feedback",
    "reorder",
    "update_weight",
    "enable_ecosystem",
)

EXPECTED_PERSIST_COLUMNS = {
    "case_folder_id",
    "owner_user_id",
    "team_id",
    "visibility",
    "search_profile_summary",
    "candidate_refs",
    "draft_descriptors",
    "title",
    "note",
    "tag",
    "status",
    "reason_code",
    "created_at",
    "updated_at",
}

PW = "sup3rsecret-pw"


def _py_files(root: Path):
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def _casebook_py_files():
    return sorted(p for p in CASEBOOK_DIR.glob("*.py"))


def _iter_import_modules(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name
        elif isinstance(node, ast.ImportFrom):
            yield node.module or ""


def _code_without_docstrings(path: Path) -> str:
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src, filename=str(path))
    blocked: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            doc = ast.get_docstring(node, clean=False)
            if doc is None or not node.body:
                continue
            body0 = node.body[0]
            if isinstance(body0, ast.Expr) and isinstance(body0.value, ast.Constant):
                blocked.update(range(body0.lineno, (body0.end_lineno or body0.lineno) + 1))
    kept: list[str] = []
    for i, line in enumerate(src.splitlines(), start=1):
        if i in blocked:
            continue
        kept.append(line.split("#", 1)[0])
    return "\n".join(kept)


def _code_without_docstrings_and_guardlists(path: Path) -> str:
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src, filename=str(path))
    blocked: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            doc = ast.get_docstring(node, clean=False)
            if doc is None or not node.body:
                continue
            body0 = node.body[0]
            if isinstance(body0, ast.Expr) and isinstance(body0.value, ast.Constant):
                blocked.update(range(body0.lineno, (body0.end_lineno or body0.lineno) + 1))
    for node in ast.walk(tree):
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        for target in targets:
            name = target.id if isinstance(target, ast.Name) else ""
            upper = name.upper()
            if "FORBIDDEN" in upper or "DENY" in upper or "BLOCK" in upper:
                blocked.update(range(node.lineno, (node.end_lineno or node.lineno) + 1))
    kept: list[str] = []
    for i, line in enumerate(src.splitlines(), start=1):
        if i in blocked:
            continue
        kept.append(line.split("#", 1)[0])
    return "\n".join(kept)


def _ast_class_fields(path: Path, class_name: str) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            fields = set()
            for stmt in node.body:
                if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                    fields.add(stmt.target.id)
            return fields
    return set()


@pytest.fixture()
def enabled_casebook_client(monkeypatch):
    pytest.importorskip("fastapi")
    pytest.importorskip("sqlmodel")
    from fastapi.testclient import TestClient
    from sqlalchemy.pool import StaticPool
    from sqlmodel import create_engine

    import app.api.auth as auth_api
    import app.api.team as team_api
    import app.casebook.router as casebook_router_mod
    from app.account.service import AuthService
    from app.account.store import AccountStore
    from app.casebook.service import CasebookService
    from app.casebook.store import CaseFolderStore
    from app.core.config import Settings
    from app.main import app
    from app.team.service import TeamService
    from app.team.store import TeamStore

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    account_store = AccountStore(engine)
    account_store.init_schema()
    casebook_store = CaseFolderStore(engine)
    casebook_store.init_schema()
    team_store = TeamStore(engine)
    team_store.init_schema()

    settings = Settings(
        DEEPSEEK_API_KEY="k",
        ENABLE_ACCOUNT_SYSTEM=True,
        ENABLE_CASEBOOK=True,
    )
    monkeypatch.setattr(auth_api, "settings", settings)
    monkeypatch.setattr(casebook_router_mod, "settings", settings)
    auth_api.set_auth_service_for_test(AuthService(account_store))
    casebook_router_mod.set_casebook_service_for_test(
        CasebookService(store=casebook_store)
    )
    team_api.set_team_service_for_test(TeamService(team_store))

    client = TestClient(app)
    yield client, engine, team_store

    auth_api.set_auth_service_for_test(None)
    casebook_router_mod.set_casebook_service_for_test(None)
    team_api.set_team_service_for_test(None)


def _register_login(client, login_name: str) -> str:
    client.post(
        "/api/auth/register",
        json={"login_name": login_name, "password": PW, "display_name": "d"},
    )
    response = client.post(
        "/api/auth/login",
        json={"login_name": login_name, "password": PW},
    )
    return response.json()["session_token"]


def _user_id(client, token: str) -> str:
    response = client.get("/api/auth/session", headers=_auth(token))
    return response.json()["account"]["user_id"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _team_auth(token: str, team_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "X-Team-Id": team_id}


def _make_team(team_store, *members: str) -> str:
    team = team_store.create_team(team_name="t", reason_code="test")
    for member in members:
        team_store.add_member(team_id=team.team_id, member_user_id=member, reason_code="test")
    return team.team_id


def _valid_candidate_ref(case_id: str = "c1") -> dict:
    return {
        "case_id": case_id,
        "case_number": "(2023)X刑初1号",
        "court": "X Court",
        "trial_level": "first",
        "case_cause": "theft",
        "judgment_date": "2023-01-01",
        "source_anchors": [
            {
                "case_id": case_id,
                "source_chunk_id": f"{case_id}_chunk0",
                "anchor_type": "case",
            }
        ],
    }


def _valid_statute_ref(statute_id: str = "s1") -> dict:
    return {
        "statute_id": statute_id,
        "law_name": "Criminal Law",
        "article_no": "264",
        "statute_anchors": [{"text_id": f"law::{statute_id}", "anchor_type": "law"}],
        "article_text": "short statute snippet",
        "source_corpus": "judge_law_corpus",
        "effective_status": "current",
        "related_case_refs": [_valid_candidate_ref("c9")],
    }


def _valid_draft_descriptor(draft_id: str = "d1") -> dict:
    return {
        "draft_id": draft_id,
        "structure_skeleton": ["Issue", "Facts", "Law"],
        "candidate_refs": [_valid_candidate_ref("c1")],
        "statute_refs": [_valid_statute_ref("s1")],
        "note": "short note",
        "tag": "tag-a",
    }


def _valid_case_folder_payload() -> dict:
    return {
        "search_profile_summary": {
            "case_cause": "theft",
            "region": "sh",
            "trial_level_preference": "first",
            "dispute_focus_keywords": ["amount"],
            "query_text": "theft amount",
        },
        "candidate_refs": [_valid_candidate_ref("c1")],
        "draft_descriptors": [_valid_draft_descriptor("d1")],
        "title": "Folder A",
        "note": "short note",
        "tag": "tag-a",
    }


def _create_folder(client, token: str) -> str:
    response = client.post(
        "/api/casebook/folders",
        json=_valid_case_folder_payload(),
        headers=_auth(token),
    )
    assert response.status_code == 200, response.text
    return response.json()["case_folder_id"]


def test_casebook_ast_imports_only_kernel_public_surface():
    offending: list[str] = []
    for path in _casebook_py_files():
        for module in _iter_import_modules(path):
            for prefix in FORBIDDEN_RETRIEVAL_PREFIXES:
                if module == prefix or module.startswith(prefix + "."):
                    offending.append(f"{path.name}: {module}")
            if module.startswith("app.kernel") and module not in ALLOWED_KERNEL_SURFACES:
                offending.append(f"{path.name}: {module}")
    assert not offending, (
        "casebook must consume only app.kernel public surfaces and must not "
        "deep-import retrieval runtime: " + "; ".join(offending)
    )


def test_casebook_product_packages_remain_mutually_isolated():
    offending: list[str] = []
    for path in _casebook_py_files():
        for module in _iter_import_modules(path):
            for other in ("intake", "statute", "drafting"):
                if module == f"app.{other}" or module.startswith(f"app.{other}."):
                    offending.append(f"{path.name}: {module}")
    for other in ("intake", "statute", "drafting"):
        other_dir = APP_DIR / other
        for path in _py_files(other_dir):
            for module in _iter_import_modules(path):
                if module == "app.casebook" or module.startswith("app.casebook."):
                    rel = str(path.relative_to(APP_DIR)).replace("\\", "/")
                    offending.append(f"{rel}: {module}")
    for path in _py_files(APP_DIR / "kernel"):
        for module in _iter_import_modules(path):
            if module == "app.casebook" or module.startswith("app.casebook."):
                rel = str(path.relative_to(APP_DIR)).replace("\\", "/")
                offending.append(f"{rel}: {module}")
    existing = {pkg for pkg in PRODUCT_PACKAGES if (APP_DIR / pkg).exists()}
    assert existing <= ALLOWED_PRODUCT_PACKAGES
    assert "casebook" in existing
    assert not offending, (
        "product packages and kernel must not import casebook across package "
        "boundaries: " + "; ".join(offending)
    )


def test_casebook_importlib_uses_kernel_contract_identities():
    guardrails = importlib.import_module("app.kernel.guardrails")
    identity = importlib.import_module("app.kernel.identity")
    service_mod = importlib.import_module("app.casebook.service")
    router_mod = importlib.import_module("app.casebook.router")

    assert service_mod.CaseFolder is guardrails.CaseFolder
    assert service_mod.sanitize_case_folder is guardrails.sanitize_case_folder
    assert service_mod.ContractViolationError is guardrails.ContractViolationError
    assert service_mod.TenantContext is identity.TenantContext

    assert router_mod.ContractViolationError is guardrails.ContractViolationError
    assert router_mod.TenantContext is identity.TenantContext
    assert router_mod.AuthResult is identity.AuthResult


def test_casebook_executable_code_has_zero_generation_backfeed_privacy_hits():
    offenders: list[str] = []
    body_tokens = (
        FORBIDDEN_CASE_BODY_TOKENS
        + FORBIDDEN_DRAFT_BODY_TOKENS
        + FORBIDDEN_OUTCOME_TOKENS
        + FORBIDDEN_PII_TOKENS
        + FORBIDDEN_CREDENTIAL_TOKENS
        + FORBIDDEN_ABSOLUTE_PHRASES
    )
    markers = tuple(m.lower() for m in TEXT_GENERATION_MARKERS + BACKFEED_MARKERS)
    for path in _casebook_py_files():
        code = _code_without_docstrings_and_guardlists(path)
        lowered = code.lower()
        for marker in markers:
            if marker in lowered:
                offenders.append(f"{path.name}: {marker}")
        for token in body_tokens:
            if token in code:
                offenders.append(f"{path.name}: {token}")
    assert not offenders, (
        "casebook executable code has unexpected generation, backfeed, body, "
        "PII, credential, or forbidden-copy hits: " + "; ".join(offenders)
    )


def test_casebook_schema_views_have_no_body_or_outcome_fields_ast():
    forbidden = set(
        FORBIDDEN_CASE_BODY_TOKENS
        + FORBIDDEN_DRAFT_BODY_TOKENS
        + FORBIDDEN_OUTCOME_TOKENS
        + FORBIDDEN_PII_TOKENS
        + FORBIDDEN_CREDENTIAL_TOKENS
    )
    offenders: list[str] = []
    tree = ast.parse((CASEBOOK_DIR / "schemas.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        for stmt in node.body:
            if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                if stmt.target.id in forbidden:
                    offenders.append(f"{node.name}.{stmt.target.id}")
    assert not offenders, (
        "casebook schemas must not expose body, outcome, PII, or credential "
        "fields: " + "; ".join(offenders)
    )


def test_case_folder_and_nested_contract_fields_stay_whitelisted_runtime():
    pytest.importorskip("pydantic_core")
    from app.contracts import (
        CANDIDATE_REF_FIELDS,
        CASE_FOLDER_E7_FIELDS,
        DRAFT_DESCRIPTOR_E6_FIELDS,
        STATUTE_FORBIDDEN_DISPLAY_KEYS,
        STATUTE_FORBIDDEN_GENERATED_KEYS,
        STATUTE_REF_FIELDS,
    )
    from app.kernel.guardrails import (
        CaseFolder,
        CaseFolderCandidateRef,
        DraftDescriptor,
        StatuteRef,
        sanitize_case_folder,
    )

    assert set(CaseFolder.model_fields) == CASE_FOLDER_E7_FIELDS
    assert set(CaseFolderCandidateRef.model_fields) == CANDIDATE_REF_FIELDS
    assert set(DraftDescriptor.model_fields) == DRAFT_DESCRIPTOR_E6_FIELDS
    assert set(StatuteRef.model_fields) == STATUTE_REF_FIELDS

    folder = sanitize_case_folder(
        {
            "case_folder_id": "cf1",
            "owner_user_id": "u1",
            **_valid_case_folder_payload(),
        }
    )

    candidate = folder.candidate_refs[0]
    draft = folder.draft_descriptors[0]
    statute = draft.statute_refs[0]

    assert set(type(candidate).model_fields) == CANDIDATE_REF_FIELDS
    assert set(type(draft).model_fields) == DRAFT_DESCRIPTOR_E6_FIELDS
    assert set(type(statute).model_fields) == STATUTE_REF_FIELDS

    candidate_leaks = set(type(candidate).model_fields) & set(
        FORBIDDEN_CASE_BODY_TOKENS + FORBIDDEN_DRAFT_BODY_TOKENS + FORBIDDEN_OUTCOME_TOKENS
    )
    draft_leaks = set(type(draft).model_fields) & set(
        FORBIDDEN_CASE_BODY_TOKENS + FORBIDDEN_DRAFT_BODY_TOKENS + FORBIDDEN_OUTCOME_TOKENS
    )
    statute_leaks = set(type(statute).model_fields) & (
        set(FORBIDDEN_CASE_BODY_TOKENS)
        | set(STATUTE_FORBIDDEN_DISPLAY_KEYS)
        | set(STATUTE_FORBIDDEN_GENERATED_KEYS)
    )

    assert not candidate_leaks
    assert not draft_leaks
    assert not statute_leaks


def test_casebook_anchorless_refs_are_fail_closed_dropped_runtime():
    pytest.importorskip("pydantic_core")
    from app.kernel.guardrails import sanitize_case_folder

    folder = sanitize_case_folder(
        {
            "case_folder_id": "cf2",
            "owner_user_id": "u1",
            "candidate_refs": [
                _valid_candidate_ref("c1"),
                {"case_id": "c2"},
                {"case_id": "c3", "source_anchors": [{"case_id": "c3"}]},
            ],
            "draft_descriptors": [
                {
                    "draft_id": "d2",
                    "structure_skeleton": ["Issue"],
                    "candidate_refs": [
                        _valid_candidate_ref("c4"),
                        {"case_id": "c5", "source_anchors": []},
                    ],
                    "statute_refs": [
                        _valid_statute_ref("s2"),
                        {"statute_id": "s3", "law_name": "Law", "statute_anchors": []},
                    ],
                }
            ],
        }
    )

    assert len(folder.candidate_refs) == 1
    assert len(folder.draft_descriptors) == 1
    assert len(folder.draft_descriptors[0].candidate_refs) == 1
    assert len(folder.draft_descriptors[0].statute_refs) == 1

    for ref in folder.candidate_refs:
        assert ref.source_anchors
    for ref in folder.draft_descriptors[0].candidate_refs:
        assert ref.source_anchors
    for ref in folder.draft_descriptors[0].statute_refs:
        assert ref.statute_anchors


@pytest.mark.parametrize(
    "payload",
    [
        {"case_summary_text": "secret summary"},
        {"win_probability": 0.9},
        {"search_profile_summary": {"raw_case": "secret raw case"}},
        {"candidate_refs": [{**_valid_candidate_ref("c1"), "judgment_text": "secret"}]},
        {
            "draft_descriptors": [
                {
                    "draft_id": "d9",
                    "structure_skeleton": ["Issue"],
                    "draft_body": "secret draft",
                }
            ]
        },
    ],
)
def test_casebook_rejects_case_summary_body_and_outcome_runtime(payload):
    pytest.importorskip("pydantic_core")
    from app.kernel.guardrails import ContractViolationError, sanitize_case_folder

    with pytest.raises(ContractViolationError):
        sanitize_case_folder(
            {
                "case_folder_id": "cf3",
                "owner_user_id": "u1",
                **payload,
            }
        )


def test_casebook_persist_model_zero_body_and_runtime_row_zero_body():
    pytest.importorskip("sqlmodel")
    from sqlalchemy.pool import StaticPool
    from sqlmodel import Session, create_engine, select

    from app.casebook.models import CaseFolderRow
    from app.casebook.service import CasebookService
    from app.casebook.store import (
        CASE_FOLDER_FORBIDDEN_PERSIST_KEYS,
        CASE_FOLDER_WRITE_ALLOWED_KEYS,
        CaseFolderStore,
    )
    from app.contracts import SEARCH_PROFILE_FIELDS
    from app.kernel.identity import TenantContext

    model_fields = _ast_class_fields(CASEBOOK_DIR / "models.py", "CaseFolderRow")
    assert model_fields == EXPECTED_PERSIST_COLUMNS

    forbidden_columns = set(
        FORBIDDEN_CASE_BODY_TOKENS
        + FORBIDDEN_DRAFT_BODY_TOKENS
        + FORBIDDEN_OUTCOME_TOKENS
        + FORBIDDEN_PII_TOKENS
        + FORBIDDEN_CREDENTIAL_TOKENS
    )
    assert not (model_fields & forbidden_columns)
    assert not (CASE_FOLDER_WRITE_ALLOWED_KEYS & forbidden_columns)

    for token in ("draft_body", "judgment_text", "raw_case", "password", "token"):
        assert token in CASE_FOLDER_FORBIDDEN_PERSIST_KEYS

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    store = CaseFolderStore(engine)
    store.init_schema()
    service = CasebookService(store=store)
    ctx = TenantContext(owner_user_id="u1", team_id=None)

    payload = _valid_case_folder_payload()
    payload["search_profile_summary"]["extra_marker"] = "drop-me"
    payload["candidate_refs"].append({"case_id": "c8"})
    payload["draft_descriptors"][0]["candidate_refs"].append({"case_id": "c9"})

    created = service.create_case_folder(ctx=ctx, payload=payload)
    assert created.visibility == "private"

    with pytest.raises(ValueError):
        store._sanitize_persist_payload({"draft_body": "x"})
    with pytest.raises(ValueError):
        store._sanitize_persist_payload({"unknown_field": "x"})

    with Session(engine) as session:
        rows = session.exec(select(CaseFolderRow)).all()
    assert len(rows) == 1
    row = rows[0]

    summary = json.loads(row.search_profile_summary or "{}")
    candidate_refs = json.loads(row.candidate_refs or "[]")
    draft_descriptors = json.loads(row.draft_descriptors or "[]")

    assert set(summary).issubset(SEARCH_PROFILE_FIELDS)
    assert "extra_marker" not in summary
    assert len(candidate_refs) == 1
    assert len(draft_descriptors) == 1
    assert len(draft_descriptors[0]["candidate_refs"]) == 1

    blob = json.dumps(
        {
            "summary": summary,
            "candidate_refs": candidate_refs,
            "draft_descriptors": draft_descriptors,
        },
        ensure_ascii=False,
    )
    for token in FORBIDDEN_CASE_BODY_TOKENS + FORBIDDEN_DRAFT_BODY_TOKENS + FORBIDDEN_OUTCOME_TOKENS:
        assert token not in blob


def test_casebook_default_private_and_object_authz_matrix_runtime(enabled_casebook_client):
    client, _engine, team_store = enabled_casebook_client

    owner_token = _register_login(client, "owner-e75@x.io")
    owner_id = _user_id(client, owner_token)
    member_token = _register_login(client, "member-e75@x.io")
    member_id = _user_id(client, member_token)
    outsider_token = _register_login(client, "outsider-e75@x.io")
    team_id = _make_team(team_store, owner_id, member_id)

    create_response = client.post(
        "/api/casebook/folders",
        json=_valid_case_folder_payload(),
        headers=_auth(owner_token),
    )
    assert create_response.status_code == 200, create_response.text
    folder_id = create_response.json()["case_folder_id"]
    assert create_response.json()["visibility"] == "private"

    private_member_get = client.get(
        f"/api/casebook/folders/{folder_id}",
        headers=_team_auth(member_token, team_id),
    )
    assert private_member_get.status_code == 404

    share_response = client.post(
        f"/api/casebook/folders/{folder_id}/share",
        json={"visibility": "team", "team_id": team_id},
        headers=_auth(owner_token),
    )
    assert share_response.status_code == 200, share_response.text
    assert share_response.json()["visibility"] == "team"

    member_get = client.get(
        f"/api/casebook/folders/{folder_id}",
        headers=_team_auth(member_token, team_id),
    )
    assert member_get.status_code == 200, member_get.text

    member_put = client.put(
        f"/api/casebook/folders/{folder_id}",
        json=_valid_case_folder_payload(),
        headers=_team_auth(member_token, team_id),
    )
    assert member_put.status_code == 404

    outsider_get = client.get(
        f"/api/casebook/folders/{folder_id}",
        headers=_auth(outsider_token),
    )
    assert outsider_get.status_code == 404


def test_casebook_visibility_enum_only_private_or_team_runtime(enabled_casebook_client):
    client, _engine, _team_store = enabled_casebook_client
    token = _register_login(client, "vis-e75@x.io")
    folder_id = _create_folder(client, token)

    response = client.post(
        f"/api/casebook/folders/{folder_id}/share",
        json={"visibility": "public", "team_id": "t1"},
        headers=_auth(token),
    )
    assert response.status_code == 422, response.text


def test_casebook_export_surface_absent_or_guarded():
    router_src = (CASEBOOK_DIR / "router.py").read_text(encoding="utf-8")
    page_src = WEB_CASEBOOK_PAGE.read_text(encoding="utf-8")

    export_present = (
        WEB_CASEBOOK_EXPORT.exists()
        or "/export" in router_src
        or "导出" in page_src
        or "download" in page_src.lower()
    )

    if not export_present:
        assert not WEB_CASEBOOK_EXPORT.exists()
        assert "/export" not in router_src
        assert "导出" not in page_src
        assert "download" not in page_src.lower()
        return

    export_src = WEB_CASEBOOK_EXPORT.read_text(encoding="utf-8")
    assert "不构成法律意见" in export_src
    assert "人工复核" in export_src

    export_code = "\n".join(
        line.split("//", 1)[0]
        for line in export_src.splitlines()
        if not line.strip().startswith("//")
    )
    for token in (
        FORBIDDEN_CASE_BODY_TOKENS
        + FORBIDDEN_DRAFT_BODY_TOKENS
        + FORBIDDEN_OUTCOME_TOKENS
    ):
        assert token not in export_code


def test_casebook_flags_default_false_no_e8_backfeed_and_router_count():
    deps = pytest.importorskip(
        "pydantic_settings",
        reason="runtime Settings check needs pydantic-settings",
    )
    del deps
    from app.core.config import Settings

    settings = Settings(_env_file=None)
    for flag in (
        "ENABLE_ECOSYSTEM",
        "ENABLE_INTAKE",
        "ENABLE_STATUTE_SEARCH",
        "ENABLE_DRAFTING",
        "ENABLE_CASEBOOK",
        "ENABLE_INTAKE_AI_EXTRACTION",
        "ENABLE_WEIGHTED_RERANK",
    ):
        assert getattr(settings, flag) is False, f"{flag} must default to false"

    assert "ENABLE_ECOSYSTEM: bool = False" in CONFIG_PY.read_text(encoding="utf-8")

    source = MAIN_PY.read_text(encoding="utf-8")
    assert source.count("app.include_router(") == 16
    assert "app.include_router(casebook_router)" in source

    offenders: list[str] = []
    for path in _casebook_py_files():
        code = _code_without_docstrings(path).lower()
        for marker in BACKFEED_MARKERS + ("internalsearchservice", "statutesearchservice"):
            if marker in code:
                offenders.append(f"{path.name}: {marker}")
    assert not offenders, (
        "casebook must not have E8-style feedback or ranking backfeed paths: "
        + "; ".join(offenders)
    )
