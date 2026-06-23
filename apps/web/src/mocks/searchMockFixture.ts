import type { SearchResponse, SourceAnchor } from "../types/search";

// FRONTEND TEST DATA ONLY.
// These entries are intentionally marked as non-real examples and must never be
// presented as genuine court cases.
export const MOCK_SEARCH_RESPONSE: SearchResponse = {
  query_session_id: "qs_mock_frontend_only",
  candidates: [],
  low_confidence_candidates: [],
  risk_hints: [
    {
      risk_type: "fact_difference",
      source_anchors: [
        mockAnchor("mock-case-002-non-real", "mock-case-002-c1", "risk_hint"),
      ],
      confidence_level: "medium",
      confidence_reasons: ["FACT_DIFFERENCE_REVIEW"],
      reason_code: "FACT_DIFFERENCE_REVIEW",
      review_note: "frontend_test_data_only",
    },
  ],
  coverage: {
    data_source: "frontend_mock_fixture",
    data_until: "unknown",
    index_version: "unknown",
    total_candidate_count: 2,
    search_mode: "standard",
    degraded_reasons: ["DATA_UNTIL_UNKNOWN", "INDEX_VERSION_UNKNOWN"],
  },
  degraded: false,
  degraded_reasons: [],
  retrieval_duration_ms: 118,
  timings: {
    rewrite_duration_ms: 0,
    embedding_duration_ms: 26,
    retrieval_duration_ms: 118,
    rerank_duration_ms: 14,
    summary_duration_ms: 39,
    total_duration_ms: 211,
  },
  results: [
    {
      case_id: "mock-case-001-non-real",
      chunk_id: "mock-case-001-c1",
      top_chunk_id: "mock-case-001-c1",
      source_chunk_ids: ["mock-case-001-c1", "mock-case-001-c2"],
      source_anchors: [
        mockAnchor("mock-case-001-non-real", "mock-case-001-c1", "result"),
        mockAnchor("mock-case-001-non-real", "mock-case-001-c2", "result", "court_opinion"),
      ],
      hit_chunk_ids: ["mock-case-001-c1"],
      retrieval_source: ["frontend_mock_fixture"],
      vector_score: 0.89,
      fallback_score: null,
      retrieval_score: 0.89,
      final_score: 0.87,
      score_breakdown: {
        vector_similarity: 0.89,
        legal_element_overlap: 0.82,
        case_cause_match: 0.8,
        key_paragraph_match: 0.75,
        authority_signal: 0.6,
        score_mode: "frontend_mock_fixture",
      },
      title: "【测试数据】产品缺陷责任纠纷样例（非真实案例）",
      case_no: "TEST-2026-MOCK-001（非真实案号）",
      court: "测试法院（非真实）",
      court_level: "基层法院",
      trial_level: "一审",
      case_cause: "产品责任纠纷",
      judgment_date: "2026-01-15",
      similarity_score: 0.87,
      confidence: "high",
      confidence_level: "high",
      confidence_reasons: [],
      confidence_score_band: "0.78-1.00",
      original_rank: 1,
      summary: {
        text: "前端测试数据：消费者主张小家电存在安全缺陷并造成损害，经营者抗辩称损害由不当使用导致。该样例用于验证结果页布局、摘要和高亮渲染。",
        source_chunk_id: "mock-case-001-c1",
        source_case_id: "mock-case-001-non-real",
        source_anchors: [
          mockAnchor("mock-case-001-non-real", "mock-case-001-c1", "summary"),
        ],
        method: "frontend_mock_fixture",
      },
      highlights: [
        {
          text: "产品是否存在缺陷、经营者是否应承担赔偿责任",
          source_chunk_id: "mock-case-001-c1",
          source_anchors: [
            mockAnchor("mock-case-001-non-real", "mock-case-001-c1", "highlight"),
          ],
          matched_terms: ["产品缺陷", "赔偿责任"],
        },
        {
          text: "经营者抗辩称损害由消费者使用方式导致",
          source_chunk_id: "mock-case-001-c2",
          source_anchors: [
            mockAnchor("mock-case-001-non-real", "mock-case-001-c2", "highlight", "court_opinion"),
          ],
          matched_terms: ["抗辩", "使用方式"],
        },
      ],
      source_url: null,
      metadata: {
        fixture_notice: "frontend_test_data_only",
      },
      matched_text: "前端测试数据片段：产品缺陷与损害因果关系争议。",
    },
    {
      case_id: "mock-case-002-non-real",
      chunk_id: "mock-case-002-c1",
      top_chunk_id: "mock-case-002-c1",
      source_chunk_ids: ["mock-case-002-c1"],
      source_anchors: [
        mockAnchor("mock-case-002-non-real", "mock-case-002-c1", "result"),
      ],
      hit_chunk_ids: ["mock-case-002-c1"],
      retrieval_source: ["frontend_mock_fixture"],
      vector_score: 0.72,
      fallback_score: null,
      retrieval_score: 0.72,
      final_score: 0.69,
      score_breakdown: {
        vector_similarity: 0.72,
        legal_element_overlap: 0.65,
        case_cause_match: 0.6,
        key_paragraph_match: 0.55,
        authority_signal: 0.5,
        score_mode: "frontend_mock_fixture",
      },
      title: "【测试数据】合同迟延履行样例（非真实案例）",
      case_no: "TEST-2026-MOCK-002（非真实案号）",
      court: "测试中级法院（非真实）",
      court_level: "中级法院",
      trial_level: "二审",
      case_cause: "买卖合同纠纷",
      judgment_date: "2026-02-20",
      similarity_score: 0.69,
      confidence: "medium",
      confidence_level: "medium",
      confidence_reasons: [],
      confidence_score_band: "0.65-0.78",
      original_rank: 2,
      summary: {
        text: "前端测试数据：合同约定分批交付，卖方持续迟延履行，双方围绕解除合同、返还款项与违约责任发生争议。",
        source_chunk_id: "mock-case-002-c1",
        source_case_id: "mock-case-002-non-real",
        source_anchors: [
          mockAnchor("mock-case-002-non-real", "mock-case-002-c1", "summary"),
        ],
        method: "frontend_mock_fixture",
      },
      highlights: [
        {
          text: "持续迟延交付导致合同目的难以实现",
          source_chunk_id: "mock-case-002-c1",
          source_anchors: [
            mockAnchor("mock-case-002-non-real", "mock-case-002-c1", "highlight"),
          ],
          matched_terms: ["迟延交付", "合同目的"],
        },
      ],
      source_url: null,
      metadata: {
        fixture_notice: "frontend_test_data_only",
      },
      matched_text: "前端测试数据片段：迟延履行与解除责任争议。",
    },
  ],
};

MOCK_SEARCH_RESPONSE.candidates = MOCK_SEARCH_RESPONSE.results;

export const MOCK_EXPAND_SEARCH_RESPONSE: SearchResponse = {
  query_session_id: "qs_mock_expand_frontend_only",
  candidates: [],
  low_confidence_candidates: [],
  risk_hints: [],
  coverage: {
    data_source: "frontend_mock_fixture",
    data_until: "unknown",
    index_version: "unknown",
    total_candidate_count: 2,
    search_mode: "expanded",
    degraded_reasons: ["BM25_FALLBACK_USED", "DATA_UNTIL_UNKNOWN", "INDEX_VERSION_UNKNOWN"],
  },
  degraded: true,
  degraded_reasons: ["BM25_FALLBACK_USED"],
  retrieval_duration_ms: 156,
  timings: {
    rewrite_duration_ms: 0,
    embedding_duration_ms: 26,
    retrieval_duration_ms: 156,
    rerank_duration_ms: 18,
    summary_duration_ms: 24,
    total_duration_ms: 246,
  },
  results: [
    {
      case_id: "mock-case-003-non-real",
      chunk_id: "mock-case-003-c1",
      top_chunk_id: "mock-case-003-c1",
      source_chunk_ids: ["mock-case-003-c1"],
      source_anchors: [
        mockAnchor("mock-case-003-non-real", "mock-case-003-c1", "result"),
      ],
      hit_chunk_ids: ["mock-case-003-c1"],
      retrieval_source: ["bm25_fallback_relaxed_recall", "frontend_mock_fixture"],
      vector_score: null,
      fallback_score: 0.58,
      retrieval_score: 0.58,
      final_score: 0.58,
      score_breakdown: {
        fallback_similarity: 0.58,
        score_mode: "frontend_mock_fixture_low_confidence",
      },
      title: "【测试数据】可能相关候选样例（非真实案例）",
      case_no: "TEST-2026-MOCK-003（非真实案号）",
      court: "测试法院（非真实）",
      court_level: "基层法院",
      trial_level: "一审",
      case_cause: "服务合同纠纷",
      judgment_date: "2026-03-12",
      similarity_score: 0.58,
      confidence: "low",
      confidence_level: "low",
      confidence_reasons: [
        "LOW_SCORE_BAND",
        "RELAXED_RECALL_SOURCE",
        "MAIN_RESULT_COUNT_BELOW_TARGET",
      ],
      confidence_score_band: "0.00-0.65",
      original_rank: 1,
      summary: {
        text: "前端测试数据：维修服务后发生安全争议，双方围绕服务瑕疵、损害原因和责任范围发生分歧。该样例仅用于验证低置信度候选展示。",
        source_chunk_id: "mock-case-003-c1",
        source_case_id: "mock-case-003-non-real",
        source_anchors: [
          mockAnchor("mock-case-003-non-real", "mock-case-003-c1", "summary"),
        ],
        method: "frontend_mock_fixture",
      },
      highlights: [
        {
          text: "服务瑕疵与损害原因存在争议",
          source_chunk_id: "mock-case-003-c1",
          source_anchors: [
            mockAnchor("mock-case-003-non-real", "mock-case-003-c1", "highlight"),
          ],
          matched_terms: ["损害原因", "责任范围"],
        },
      ],
      source_url: null,
      metadata: {
        fixture_notice: "frontend_test_data_only",
        confidence_notice: "low_confidence_candidate",
      },
      matched_text: "前端测试数据片段：服务瑕疵、损害原因和责任范围争议。",
    },
    {
      case_id: "mock-case-004-non-real",
      chunk_id: "mock-case-004-c1",
      top_chunk_id: "mock-case-004-c1",
      source_chunk_ids: ["mock-case-004-c1"],
      source_anchors: [
        mockAnchor("mock-case-004-non-real", "mock-case-004-c1", "result"),
      ],
      hit_chunk_ids: ["mock-case-004-c1"],
      retrieval_source: ["bm25_fallback_relaxed_recall", "frontend_mock_fixture"],
      vector_score: null,
      fallback_score: 0.54,
      retrieval_score: 0.54,
      final_score: 0.54,
      score_breakdown: {
        fallback_similarity: 0.54,
        score_mode: "frontend_mock_fixture_low_confidence",
      },
      title: "【测试数据】损害因果关系候选样例（非真实案例）",
      case_no: "TEST-2026-MOCK-004（非真实案号）",
      court: "测试中级法院（非真实）",
      court_level: "中级法院",
      trial_level: "二审",
      case_cause: "侵权责任纠纷",
      judgment_date: "2026-04-18",
      similarity_score: 0.54,
      confidence: "low",
      confidence_level: "low",
      confidence_reasons: [
        "LOW_SCORE_BAND",
        "RELAXED_RECALL_SOURCE",
        "MAIN_RESULT_COUNT_BELOW_TARGET",
      ],
      confidence_score_band: "0.00-0.65",
      original_rank: 2,
      summary: {
        text: "前端测试数据：当事人围绕损害发生原因、举证责任和赔偿范围争议较大，事实动作与主案情仅部分相近。",
        source_chunk_id: "mock-case-004-c1",
        source_case_id: "mock-case-004-non-real",
        source_anchors: [
          mockAnchor("mock-case-004-non-real", "mock-case-004-c1", "summary"),
        ],
        method: "frontend_mock_fixture",
      },
      highlights: [
        {
          text: "损害发生原因与举证责任需要复核",
          source_chunk_id: "mock-case-004-c1",
          source_anchors: [
            mockAnchor("mock-case-004-non-real", "mock-case-004-c1", "highlight"),
          ],
          matched_terms: ["损害", "举证责任"],
        },
      ],
      source_url: null,
      metadata: {
        fixture_notice: "frontend_test_data_only",
        confidence_notice: "low_confidence_candidate",
      },
      matched_text: "前端测试数据片段：损害原因、举证责任和赔偿范围争议。",
    },
  ],
};

MOCK_EXPAND_SEARCH_RESPONSE.low_confidence_candidates = MOCK_EXPAND_SEARCH_RESPONSE.results;
MOCK_EXPAND_SEARCH_RESPONSE.results = [];
MOCK_EXPAND_SEARCH_RESPONSE.candidates = [];

function mockAnchor(
  caseId: string,
  chunkId: string,
  anchorType: SourceAnchor["anchor_type"],
  chunkType = "court_found"
): SourceAnchor {
  return {
    case_id: caseId,
    source_chunk_id: chunkId,
    chunk_type: chunkType,
    anchor_type: anchorType,
    source_ref: "frontend_mock_fixture",
  };
}
