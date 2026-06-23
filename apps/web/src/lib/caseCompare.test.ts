import { describe, expect, it } from "vitest";

import {
  buildCaseCompare,
  COMPARE_SECTION_TITLES,
  MAX_COMPARE_CASES,
  summarizeCaseCompare,
  type CaseCompareSource,
} from "./caseCompare";
import type {
  CaseDetailResponse,
  FactAlignmentResponse,
  RiskHint,
  SearchResultItem,
  SourceAnchor,
} from "../types/search";

function anchor(caseId: string, chunkId: string, type = "detail_chunk"): SourceAnchor {
  return { case_id: caseId, source_chunk_id: chunkId, anchor_type: type };
}

function makeDetail(overrides: Partial<CaseDetailResponse> & { case_id: string }): CaseDetailResponse {
  return {
    case_no: "(2021)京01民初1号",
    court: "北京一中院",
    trial_level: "一审",
    case_cause: "借款合同纠纷",
    judgment_date: "2021-06-01",
    chunks: [
      { chunk_id: `${overrides.case_id}-c1`, text: "x" },
      { chunk_id: `${overrides.case_id}-c2`, text: "y" },
    ],
    degraded: false,
    degraded_reasons: [],
    timings: {
      rewrite_duration_ms: 0,
      embedding_duration_ms: 0,
      retrieval_duration_ms: 0,
      rerank_duration_ms: 0,
      summary_duration_ms: 0,
      total_duration_ms: 0,
    },
    ...overrides,
  };
}

function seed(caseId: string): SearchResultItem {
  return {
    case_id: caseId,
    source_chunk_ids: [],
    hit_chunk_ids: [],
    retrieval_source: [],
    score_breakdown: {},
    case_no: "(2021)京01民初1号",
    court: "北京一中院",
    case_cause: "借款合同纠纷",
    judgment_date: "2021-06-01",
    highlights: [],
    metadata: {},
  };
}

describe("buildCaseCompare", () => {
  it("builds five sections in fixed order for the selected cases", () => {
    const sources: CaseCompareSource[] = [
      { caseId: "case-1", seed: seed("case-1"), detail: makeDetail({ case_id: "case-1" }) },
      { caseId: "case-2", seed: seed("case-2"), detail: makeDetail({ case_id: "case-2" }) },
    ];
    const compare = buildCaseCompare(sources);
    expect(compare.selectedCaseIds).toEqual(["case-1", "case-2"]);
    expect(compare.compareSections.map((s) => s.key)).toEqual([
      "metadata",
      "holding_summary",
      "issue_focus",
      "fact_dimension",
      "risk_hints",
    ]);
    expect(COMPARE_SECTION_TITLES.metadata).toBe("元数据");
  });

  it("shows holding entries only when anchored to a navigable chunk of the same case", () => {
    const detail = makeDetail({
      case_id: "case-1",
      holding_summary: {
        summary_items: [
          { text: "认定A", source_anchors: [anchor("case-1", "case-1-c1", "holding")] },
          { text: "无锚点B", source_anchors: [] },
          { text: "跨案C", source_anchors: [anchor("case-9", "case-9-c1", "holding")] },
          { text: "不可定位D", source_anchors: [anchor("case-1", "case-1-cX", "holding")] },
        ],
        source_anchors: [],
        confidence: "high",
        generation_status: "generated",
      },
    });
    const compare = buildCaseCompare([{ caseId: "case-1", detail }]);
    const holding = compare.compareSections.find((s) => s.key === "holding_summary")!;
    const cell = holding.cells[0];
    expect(cell.status).toBe("available");
    expect(cell.entries).toHaveLength(1);
    expect(cell.entries[0].text).toBe("认定A");
    expect(cell.entries[0].anchor.source_chunk_id).toBe("case-1-c1");
  });

  it("degrades holding with module_degraded when generation_status is not generated", () => {
    const detail = makeDetail({
      case_id: "case-1",
      holding_summary: {
        summary_items: [],
        source_anchors: [],
        confidence: "low",
        generation_status: "degraded",
        degrade_reason: "missing_source_anchor",
      },
    });
    const compare = buildCaseCompare([{ caseId: "case-1", detail }]);
    const cell = compare.compareSections.find((s) => s.key === "holding_summary")!.cells[0];
    expect(cell.status).toBe("degraded");
    expect(cell.degradeReason).toBe("module_degraded");
    expect(cell.entries).toHaveLength(0);
  });

  it("degrades to no_anchored_content when items exist but none carry a usable anchor", () => {
    const detail = makeDetail({
      case_id: "case-1",
      holding_summary: {
        summary_items: [{ text: "认定A", source_anchors: [] }],
        source_anchors: [],
        confidence: "high",
        generation_status: "generated",
      },
    });
    const cell = buildCaseCompare([{ caseId: "case-1", detail }]).compareSections.find(
      (s) => s.key === "holding_summary"
    )!.cells[0];
    expect(cell.status).toBe("degraded");
    expect(cell.degradeReason).toBe("no_anchored_content");
  });

  it("marks fact dimension loading while alignment is still loading", () => {
    const detail = makeDetail({ case_id: "case-1" });
    const cell = buildCaseCompare([
      { caseId: "case-1", detail, factAlignmentLoading: true },
    ]).compareSections.find((s) => s.key === "fact_dimension")!.cells[0];
    expect(cell.status).toBe("loading");
    expect(cell.degradeReason).toBe("detail_loading");
  });

  it("builds fact dimension from anchored, same-case facts", () => {
    const detail = makeDetail({ case_id: "case-1" });
    const factAlignment: FactAlignmentResponse = {
      case_id: "case-1",
      generation_status: "generated",
      query_signal_present: true,
      items: [
        {
          dimension: "交易金额",
          dimension_key: "amount",
          query_side_signal: "input_signals_dimension",
          case_side_facts: ["本金100万元"],
          source_anchors: [anchor("case-1", "case-1-c2")],
          match_type: "same_dimension",
        },
        {
          dimension: "无锚点维度",
          dimension_key: "x",
          query_side_signal: "input_signals_dimension",
          case_side_facts: ["something"],
          source_anchors: [],
          match_type: "difference_to_review",
        },
      ],
    };
    const cell = buildCaseCompare([
      { caseId: "case-1", detail, factAlignment },
    ]).compareSections.find((s) => s.key === "fact_dimension")!.cells[0];
    expect(cell.status).toBe("available");
    expect(cell.entries).toHaveLength(1);
    expect(cell.entries[0].label).toBe("交易金额");
  });

  it("filters risk hints to the same case and degrades to no_flagged_risk when none", () => {
    const detail1 = makeDetail({ case_id: "case-1" });
    const detail2 = makeDetail({ case_id: "case-2" });
    const riskHints: RiskHint[] = [
      {
        risk_type: "fact_difference",
        source_anchors: [anchor("case-1", "case-1-c1")],
        confidence_level: "low",
        confidence_reasons: [],
        reason_code: "FACT_DIFF",
      },
    ];
    const compare = buildCaseCompare(
      [
        { caseId: "case-1", detail: detail1 },
        { caseId: "case-2", detail: detail2 },
      ],
      riskHints
    );
    const risk = compare.compareSections.find((s) => s.key === "risk_hints")!;
    expect(risk.cells[0].status).toBe("available");
    expect(risk.cells[0].entries[0].label).toBe("fact_difference");
    expect(risk.cells[1].status).toBe("degraded");
    expect(risk.cells[1].degradeReason).toBe("no_flagged_risk");
  });

  it("degrades all AI sections to detail_unavailable when detail failed to load", () => {
    const compare = buildCaseCompare([{ caseId: "case-1", seed: seed("case-1"), detail: null }]);
    const holding = compare.compareSections.find((s) => s.key === "holding_summary")!.cells[0];
    expect(holding.status).toBe("degraded");
    expect(holding.degradeReason).toBe("detail_unavailable");
    // metadata still available from seed record
    const meta = compare.compareSections.find((s) => s.key === "metadata")!.cells[0];
    expect(meta.status).toBe("available");
  });

  it("every rendered anchor belongs to its own case (no cross-case borrowing)", () => {
    const detail = makeDetail({
      case_id: "case-1",
      holding_summary: {
        summary_items: [{ text: "A", source_anchors: [anchor("case-1", "case-1-c1", "holding")] }],
        source_anchors: [],
        confidence: "high",
        generation_status: "generated",
      },
    });
    const compare = buildCaseCompare([{ caseId: "case-1", detail }]);
    for (const list of Object.values(compare.sourceAnchors)) {
      for (const a of list) {
        expect(a.case_id).toBe("case-1");
      }
    }
  });

  it("summarize emits only counts/status/reason codes (no body text)", () => {
    const detail = makeDetail({ case_id: "case-1" });
    const compare = buildCaseCompare([{ caseId: "case-1", detail }]);
    const summary = summarizeCaseCompare(compare);
    const serialized = JSON.stringify(summary);
    expect(serialized).not.toContain("借款合同");
    expect(serialized).not.toContain("北京一中院");
    expect(summary.selected_case_count).toBe(1);
    expect(summary.section_count).toBe(5);
    expect(summary.by_section.metadata.available).toBe(1);
  });

  it("exposes a controlled max-cases constant", () => {
    expect(MAX_COMPARE_CASES).toBeGreaterThanOrEqual(2);
    expect(MAX_COMPARE_CASES).toBeLessThanOrEqual(4);
  });
});
