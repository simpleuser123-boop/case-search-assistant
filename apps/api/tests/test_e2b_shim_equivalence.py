"""E-2b 旧路径 shim 等价性守门测试。

E-2b 把四组内核成员物理迁入 app.kernel.<group>.<pkg>/，旧路径（app.retrieval、
app.account、...）改为 re-export shim。本测试断言「旧路径符号 is 新路径符号」——
即旧 import 路径与内核新位置指向同一对象（模块对象与公开符号均同一），
从而证明 shim 零行为分叉：任何遗漏的旧路径引用与走新路径行为完全一致。

迁移基线：E-2a 末态（docs/development/e2a-release-gate-20260615-095700.json）。
本测试只做对象身份比对，不触发排序/检索/DB 写入，无正文、无凭据。
"""
from __future__ import annotations

import importlib

import pytest

PACKAGE_SHIM_MAP = {
    "app.retrieval": "app.kernel.rag.retrieval",
    "app.rerank": "app.kernel.rag.rerank",
    "app.query_processing": "app.kernel.rag.query_processing",
    "app.summary": "app.kernel.rag.summary",
    "app.account": "app.kernel.identity.account",
    "app.team": "app.kernel.identity.team",
    "app.permission": "app.kernel.identity.permission",
    "app.sharing": "app.kernel.identity.sharing",
    "app.contracts": "app.kernel.guardrails.contracts",
    "app.pipeline": "app.kernel.data.pipeline",
    "app.case_store": "app.kernel.data.case_store",
}

SUBMODULE_SHIM_MAP = {
    "app.retrieval.models": "app.kernel.rag.retrieval.models",
    "app.retrieval.service": "app.kernel.rag.retrieval.service",
    "app.retrieval.confidence": "app.kernel.rag.retrieval.confidence",
    "app.retrieval.chroma_adapter": "app.kernel.rag.retrieval.chroma_adapter",
    "app.retrieval.bm25_fallback": "app.kernel.rag.retrieval.bm25_fallback",
    "app.retrieval.embedding": "app.kernel.rag.retrieval.embedding",
    "app.retrieval.risk_hints": "app.kernel.rag.retrieval.risk_hints",
    "app.rerank.models": "app.kernel.rag.rerank.models",
    "app.query_processing.models": "app.kernel.rag.query_processing.models",
    "app.query_processing.term_mapping": "app.kernel.rag.query_processing.term_mapping",
    "app.summary.highlights": "app.kernel.rag.summary.highlights",
    "app.account.models": "app.kernel.identity.account.models",
    "app.account.service": "app.kernel.identity.account.service",
    "app.account.store": "app.kernel.identity.account.store",
    "app.team.store": "app.kernel.identity.team.store",
    "app.team.service": "app.kernel.identity.team.service",
    "app.team.isolation": "app.kernel.identity.team.isolation",
    "app.team.models": "app.kernel.identity.team.models",
    "app.permission.access": "app.kernel.identity.permission.access",
    "app.permission.service": "app.kernel.identity.permission.service",
    "app.permission.store": "app.kernel.identity.permission.store",
    "app.sharing.anchors": "app.kernel.identity.sharing.anchors",
    "app.sharing.service": "app.kernel.identity.sharing.service",
    "app.sharing.store": "app.kernel.identity.sharing.store",
    "app.case_store.jsonl_store": "app.kernel.data.case_store.jsonl_store",
    "app.pipeline.index_chroma": "app.kernel.data.pipeline.index_chroma",
}


@pytest.mark.parametrize("old_path,new_path", sorted(PACKAGE_SHIM_MAP.items()))
def test_old_package_path_still_importable(old_path, new_path):
    old_mod = importlib.import_module(old_path)
    new_mod = importlib.import_module(new_path)
    assert old_mod is not None
    assert new_mod is not None


@pytest.mark.parametrize("old_path,new_path", sorted(SUBMODULE_SHIM_MAP.items()))
def test_submodule_shim_is_same_object(old_path, new_path):
    old_mod = importlib.import_module(old_path)
    new_mod = importlib.import_module(new_path)
    assert old_mod is new_mod, (
        "shim behavior fork: " + old_path + " is not " + new_path
    )


@pytest.mark.parametrize("old_path,new_path", sorted(PACKAGE_SHIM_MAP.items()))
def test_public_symbols_are_identical_objects(old_path, new_path):
    old_mod = importlib.import_module(old_path)
    new_mod = importlib.import_module(new_path)
    names = getattr(new_mod, "__all__", None) or [
        n for n in dir(new_mod) if not n.startswith("_")
    ]
    mismatches = []
    for name in names:
        if not hasattr(new_mod, name):
            continue
        new_obj = getattr(new_mod, name)
        if not hasattr(old_mod, name):
            mismatches.append(old_path + "." + name + " missing")
            continue
        old_obj = getattr(old_mod, name)
        if old_obj is not new_obj:
            mismatches.append(old_path + "." + name + " not-identical")
    assert not mismatches, "shim public symbol identity fork: " + "; ".join(mismatches)


def test_kernel_surface_symbol_identity_against_old_paths():
    import app.kernel as k

    checks = {
        "CaseCandidate": "app.retrieval",
        "VectorRetrievalService": "app.retrieval",
        "FactSimilarityReranker": "app.rerank",
        "QueryProcessingService": "app.query_processing",
        "SummaryService": "app.summary",
        "AuthService": "app.account.service",
        "TeamService": "app.team.service",
        "PermissionService": "app.permission.service",
        "SharingService": "app.sharing.service",
        "sanitize_contract": "app.contracts",
        "authorize": "app.permission.access",
        "validate_anchors_for_share": "app.sharing.anchors",
        "get_case_detail": "app.case_store.jsonl_store",
    }
    mismatches = []
    for sym, old_path in checks.items():
        old_mod = importlib.import_module(old_path)
        if not hasattr(k, sym) or not hasattr(old_mod, sym):
            mismatches.append(sym + " missing")
            continue
        if getattr(k, sym) is not getattr(old_mod, sym):
            mismatches.append("app.kernel." + sym + " vs " + old_path + "." + sym + " not-identical")
    assert not mismatches, "kernel surface vs old-path identity fork: " + "; ".join(mismatches)
