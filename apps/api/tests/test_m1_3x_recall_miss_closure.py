from __future__ import annotations

import pytest

from scripts.m1_3x_recall_miss_closure import _classify, _privacy_check


def _presence(hit: bool, rank: int | None = None) -> dict:
    return {
        "hit": hit,
        "bestRank": rank,
        "caseId": "case-target" if hit else None,
        "relevance": 3 if hit else None,
    }


def _snapshot() -> dict:
    return {
        "channels": {
            "originalVector": {"presence": _presence(True, 15)},
            "mappedRewriteVector": {"presence": _presence(False)},
            "recallOnlyMappingVector": {"presence": _presence(False)},
            "cleanedBm25": {"presence": _presence(True, 30)},
            "expandedBm25": {"presence": _presence(False)},
            "controlledBm25Supplement": {"presence": _presence(False)},
        },
        "pool": {
            "finalPresence": _presence(True, 15),
            "mergedPresence": _presence(True, 15),
            "mergeDropped": False,
            "dedupeDropped": False,
            "gatingDropped": True,
        },
    }


def test_classify_marks_merged_outside_top10_as_explained_not_fixed():
    miss_types, outcome, reason_codes, fixed = _classify(
        target_case_ids=["case-target"],
        qrel_missing_from_corpus=[],
        snapshot=_snapshot(),
        variant_probes=[],
    )

    assert outcome == "EXPLAINED_NOT_FIXED"
    assert fixed is False
    assert "GATING_DROPPED" in miss_types
    assert "UNREPAIRABLE_WITH_CURRENT_SIGNALS" in miss_types
    assert "RELEVANT_CASE_IN_POOL_BUT_RANKING_OR_SIGNAL_STRENGTH_OUT_OF_SCOPE" in reason_codes


def test_privacy_check_rejects_raw_query_and_forbidden_text_fields():
    with pytest.raises(ValueError, match="raw query"):
        _privacy_check(markdown="raw query sample", json_text="{}", raw_queries=["raw query sample"])

    with pytest.raises(ValueError, match="forbidden field"):
        _privacy_check(markdown="", json_text='{"chunk_text": "x"}', raw_queries=[])
