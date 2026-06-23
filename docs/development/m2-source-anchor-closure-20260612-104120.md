# M2-2 Source Anchor Closure

- Generated at: `2026-06-12T10:41:20+08:00`
- Step: `M2-2 来源锚点链路闭环`
- Scope: source anchor contract, backend safe filtering, frontend anchor display, focused tests, report only.
- Privacy: this report contains no original query text, case fact body, candidate body, or chunk body.

## M2-1 Entry Contract Reference

Latest M2-1 entry contract:

- `docs/development/m2-entry-contract-20260612-100850.md`
- `docs/development/m2-entry-contract-20260612-100850.json`

Entry gate result: `GO`.

M2-2 continues these frozen boundaries:

| Boundary | Status |
| --- | --- |
| `ENABLE_WEIGHTED_RERANK` default | `false` |
| online sorting change | `none` |
| source selection change | `none` |
| qrels / labels / historical eval artifacts | `not modified` |
| M2-3 to M2-8 capabilities | `not implemented` |

## Current Source Field Survey

| Link | Existing Fields Before M2-2 | M2-2 Handling |
| --- | --- | --- |
| retrieval chunk | `case_id`, `chunk_id`, `metadata`, `text`, `source`, `retrieval_source` | `case_id` and `chunk_id` remain the only source id basis; `source` can become `source_ref` |
| merged candidate | `case_id`, `top_chunk_id`, `source_chunk_ids`, `hit_chunk_ids`, `metadata`, `matched_text`, `source` | result-level anchors are built from existing chunk ids only |
| search summary | `text`, `source_chunk_id`, `source_case_id`, `method`, optional `degraded_reason` | summary is returned only when a summary anchor can be built |
| search highlights | `text`, `source_chunk_id`, offsets, matched term list, reason | highlights without anchors are filtered out |
| case detail chunks | `chunk_id`, `chunk_type`, offsets, `text`; case-level `source_url` / `source_name` | each detail chunk receives a `detail_chunk` source anchor when `chunk_id` exists |
| frontend result card | used `source_chunk_ids`, summary source id, highlight source id | now requires `source_anchors` for summary, highlight, and source chips |
| frontend detail drawer | checked summary chunk id and rendered detail source chunks | now requires detail chunk anchors and summary anchors |

AI-processed generation/display positions reviewed:

| Position | Display Rule |
| --- | --- |
| search result summary | show only with `summary.source_anchors` |
| search result highlights | show only with per-highlight `source_anchors` |
| fallback source snippet | show only when the result has result-level anchors |
| detail summary from seed result | show only when summary anchor matches a returned detail chunk anchor |
| detail source excerpts | show only when detail chunk has `detail_chunk` anchor |
| generated legal explanation / risk hint | not implemented in this step |

## `source_anchors` Minimum Contract

Every source anchor has this shape:

| Field | Required | Notes |
| --- | --- | --- |
| `case_id` | yes | existing case id only |
| `source_chunk_id` | yes | existing chunk id only; no fabricated id |
| `chunk_type` | field present, nullable | copied from metadata or detail chunk when available |
| `anchor_type` | yes | `result`, `summary`, `highlight`, or `detail_chunk` |
| `source_url` | nullable | copied from existing source metadata |
| `source_ref` | nullable fallback | existing source name/source marker, or local store marker when no URL exists |

## Anchor Processing Rules

| Area | Rule |
| --- | --- |
| result card | show source entry only from result-level `source_anchors` |
| summary | show only when text and `summary` anchor both exist |
| highlight | show only when text and `highlight` anchor both exist |
| detail summary | show seed summary only when its summary anchor matches a returned detail chunk |
| detail source chunk | show chunk text only when the chunk has `detail_chunk` anchor |
| unanchored generated content | filter or hide; do not re-label as inferred content |
| source id creation | do not create source_chunk_id when upstream data lacks it |

## Downgrade Strategy

| Missing Condition | User-Visible Downgrade |
| --- | --- |
| summary lacks anchor | hide summary text and show safe empty state |
| highlight lacks anchor | hide highlight item |
| result lacks source anchor | hide matched-text fallback and show safe empty state |
| detail chunk lacks anchor | hide detail chunk excerpt |
| detail summary anchor does not match detail chunk | fallback to anchored source chunk or empty state |
| source URL unavailable | show source ref/source chunk id instead of claiming an external URL |

## Implementation Summary

Backend changes:

- Added `SourceAnchor` schema.
- Added `source_anchors` to search result items and detail chunks.
- Added API-layer filtering for unanchored summary and highlight content.
- Added detail chunk anchor construction in the JSONL case store.
- Kept sorting, source selection, rerank defaults, qrels, labels, and historical evaluation artifacts unchanged.

Frontend changes:

- Added `SourceAnchor` type.
- Result cards require anchors before rendering summaries, highlights, source chips, or matched-text fallback.
- Detail drawer requires anchors before rendering seed summary and detail source excerpts.
- Frontend mock search/detail fixtures now carry the same anchor contract for local UI validation.
- Focused tests cover visible anchor entry and unanchored content hiding.

## Tests And Validation

| Command | Result |
| --- | --- |
| `cd apps/api; pytest tests/test_summary_service.py tests/test_search_api_fallback_smoke.py tests/test_m2_source_anchor_closure.py tests/test_health.py` | `26 passed` |
| `cd apps/web; npm run test` | `40 passed` |
| `cd apps/web; npm run build` | `passed` |

Additional UI acceptance note: a temporary Vite server started successfully on localhost, but Playwright CLI browser launch was blocked because the required test browser was not installed and no local Edge/Chrome executable was found. The temporary server was stopped. This did not change the required command results above.

Focused test coverage added:

| Test Area | Status |
| --- | --- |
| unanchored generated summary/highlight filtered | `passed` |
| search result processed fields carry source anchors | `passed` |
| detail chunks carry traceable anchors | `passed` |
| frontend source entry visible when anchors exist | `passed` |
| frontend unanchored generated content hidden | `passed` |
| log capture excludes raw query/chunk body sentinels | `passed` |

## Body Leakage Check

| Artifact Layer | Result |
| --- | --- |
| backend logs in focused smoke | no raw query or chunk body sentinel observed |
| frontend analytics tests | no raw query or detail chunk body in events |
| Markdown report | no original query, case fact body, candidate body, or chunk body |
| JSON report | sanitized ids, field names, counts, status, reason codes, and results only |

## Go / No-Go

| Gate | Result |
| --- | --- |
| M2-1 artifact exists and conclusion is GO | `GO` |
| `ENABLE_WEIGHTED_RERANK=false` remains true | `GO` |
| source anchors trace to `case_id` and `source_chunk_id` | `GO` |
| user-visible AI-processed summary/highlight requires anchors | `GO` |
| unanchored generated content is hidden or downgraded | `GO` |
| no qrels/label/history eval changes | `GO` |
| no online sorting/source selection/rerank default changes | `GO` |
| report / JSON body leakage | `GO` |

Overall conclusion: `GO`.

Next allowed step:

```text
类案检索助手 M2-3 数据覆盖声明与隐私展示
```
