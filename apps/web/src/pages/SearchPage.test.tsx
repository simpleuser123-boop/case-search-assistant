import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { StrictMode } from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SearchPage } from "./SearchPage";
import type { CaseDetailResponse, SearchResponse, SourceAnchor } from "../types/search";

const validQuery =
  "消费者购买电热水壶后出现漏电受伤，商家称系使用不当，双方争议产品缺陷与赔偿责任。";

const baseResponse: SearchResponse = {
  query_session_id: "qs_test_001",
  candidates: [],
  low_confidence_candidates: [],
  risk_hints: [],
  coverage: {
    data_source: "测试来源库",
    data_until: "unknown",
    index_version: "case_chunks_bge_m3_v1",
    total_candidate_count: 2,
    search_mode: "standard",
    degraded_reasons: ["DATA_UNTIL_UNKNOWN"],
  },
  degraded: false,
  degraded_reasons: [],
  retrieval_duration_ms: 80,
  timings: {
    rewrite_duration_ms: 0,
    embedding_duration_ms: 15,
    retrieval_duration_ms: 80,
    rerank_duration_ms: 12,
    summary_duration_ms: 20,
    total_duration_ms: 130,
  },
  results: [
    {
      case_id: "case-001",
      chunk_id: "case-001-c1",
      top_chunk_id: "case-001-c1",
      source_chunk_ids: ["case-001-c1"],
      source_anchors: [sourceAnchor("case-001", "case-001-c1", "result")],
      hit_chunk_ids: ["case-001-c1"],
      retrieval_source: ["chroma_vector"],
      vector_score: 0.87,
      fallback_score: null,
      retrieval_score: 0.87,
      final_score: 0.87,
      score_breakdown: {
        vector_similarity: 0.87,
        legal_element_overlap: 0.8,
        case_cause_match: 0.7,
      },
      title: "产品责任纠纷判决书",
      case_no: "（2025）测01民初1号",
      court: "测试人民法院",
      court_level: "基层法院",
      trial_level: "一审",
      case_cause: "产品责任纠纷",
      judgment_date: "2025-05-01",
      similarity_score: 0.87,
      confidence: "high",
      confidence_level: "high",
      confidence_reasons: [],
      confidence_score_band: "0.78-1.00",
      original_rank: 1,
      summary: {
        text: "消费者主张产品存在缺陷并造成损害，经营者抗辩称由不当使用导致。",
        source_chunk_id: "case-001-c1",
        source_case_id: "case-001",
        source_anchors: [sourceAnchor("case-001", "case-001-c1", "summary")],
        method: "extractive",
      },
      highlights: [
        {
          text: "产品是否存在缺陷、经营者是否应承担赔偿责任",
          source_chunk_id: "case-001-c1",
          source_anchors: [sourceAnchor("case-001", "case-001-c1", "highlight")],
        },
      ],
      source_url: null,
      metadata: {},
      matched_text: "产品缺陷争议片段",
    },
  ],
};

const lowConfidenceCandidate: SearchResponse["results"][number] = {
  ...baseResponse.results[0],
  case_id: "case-expand-001",
  chunk_id: "case-expand-c1",
  top_chunk_id: "case-expand-c1",
  source_chunk_ids: ["case-expand-c1"],
  source_anchors: [sourceAnchor("case-expand-001", "case-expand-c1", "result")],
  hit_chunk_ids: ["case-expand-c1"],
  retrieval_source: ["bm25_fallback_relaxed_recall"],
  vector_score: null,
  fallback_score: 0.58,
  retrieval_score: 0.58,
  final_score: 0.58,
  score_breakdown: {
    fallback_similarity: 0.58,
    score_mode: "test_low_confidence",
  },
  title: "可能相关产品责任候选",
  case_no: "（2025）测01民初9号",
  similarity_score: 0.58,
  confidence: "low",
  confidence_level: "low",
  confidence_reasons: ["LOW_SCORE_BAND", "MAIN_RESULT_COUNT_BELOW_TARGET"],
  confidence_score_band: "0.00-0.65",
  original_rank: 2,
  summary: {
    text: "候选案例仅与损害原因和责任争议部分相关，需要人工复核。",
    source_chunk_id: "case-expand-c1",
    source_case_id: "case-expand-001",
    source_anchors: [sourceAnchor("case-expand-001", "case-expand-c1", "summary")],
    method: "test_fixture",
  },
  highlights: [
    {
      text: "损害原因和责任争议部分相关",
      source_chunk_id: "case-expand-c1",
      source_anchors: [sourceAnchor("case-expand-001", "case-expand-c1", "highlight")],
    },
  ],
  matched_text: "候选来源片段：损害原因和责任争议部分相关。",
};

const expandedResponse: SearchResponse = {
  ...baseResponse,
  query_session_id: "qs_expand_001",
  coverage: {
    ...baseResponse.coverage,
    total_candidate_count: 1,
    search_mode: "expanded",
    degraded_reasons: ["BM25_FALLBACK_USED", "DATA_UNTIL_UNKNOWN"],
  },
  degraded: true,
  degraded_reasons: ["BM25_FALLBACK_USED"],
  results: [],
  low_confidence_candidates: [lowConfidenceCandidate],
  candidates: [],
};

const baseCaseDetail: CaseDetailResponse = {
  query_session_id: "qs_detail_001",
  case_id: "case-001",
  title: "产品责任纠纷判决书",
  case_no: "（2025）测01民初1号",
  court: "测试人民法院",
  court_level: "基层法院",
  trial_level: "一审",
  case_cause: "产品责任纠纷",
  judgment_date: "2025-05-01",
  region: "测试地区",
  source_url: "https://example.test/case-001",
  source_name: "测试来源库",
  degraded: false,
  degraded_reasons: [],
  timings: baseResponse.timings,
  holding_summary: {
    summary_items: [
      {
        text: "法院围绕产品缺陷、损害原因和经营者举证情况整理裁判说理，供阅读复核。",
        source_anchors: [
          sourceAnchor("case-001", "case-001-c2", "detail_chunk", "court_opinion"),
        ],
        confidence: "medium",
      },
    ],
    source_anchors: [
      sourceAnchor("case-001", "case-001-c2", "detail_chunk", "court_opinion"),
    ],
    confidence: "medium",
    generation_status: "generated",
    degrade_reason: null,
  },
  issue_focus: {
    items: [
      {
        label: "围绕产品缺陷、损害原因的争议复核",
        category: "争议焦点",
        source_anchors: [
          sourceAnchor("case-001", "case-001-c1", "detail_chunk", "court_found"),
        ],
        confidence: "medium",
        degrade_reason: null,
      },
    ],
    source_anchors: [
      sourceAnchor("case-001", "case-001-c1", "detail_chunk", "court_found"),
    ],
    generation_status: "generated",
    degrade_reason: null,
  },
  key_elements: {
    items: [
      {
        label: "关键要素：产品缺陷、因果关系相关说理",
        category: "法院认定的关键要素",
        source_anchors: [
          sourceAnchor("case-001", "case-001-c2", "detail_chunk", "court_opinion"),
        ],
        confidence: "medium",
        degrade_reason: null,
      },
      {
        label: "程序或证据节点：举证相关材料",
        category: "与用户阅读相关的程序或证据节点",
        source_anchors: [
          sourceAnchor("case-001", "case-001-c2", "detail_chunk", "court_opinion"),
        ],
        confidence: "low",
        degrade_reason: null,
      },
    ],
    source_anchors: [
      sourceAnchor("case-001", "case-001-c2", "detail_chunk", "court_opinion"),
    ],
    generation_status: "generated",
    degrade_reason: null,
  },
  chunks: [
    {
      chunk_id: "case-001-c1",
      chunk_type: "court_found",
      source_anchors: [sourceAnchor("case-001", "case-001-c1", "detail_chunk")],
      start_offset: 0,
      end_offset: 80,
      text: "法院查明：消费者购买电热水壶后发生漏电受伤，双方围绕产品缺陷和损害原因存在争议。",
    },
    {
      chunk_id: "case-001-c2",
      chunk_type: "court_opinion",
      source_anchors: [
        sourceAnchor("case-001", "case-001-c2", "detail_chunk", "court_opinion"),
      ],
      start_offset: 81,
      end_offset: 180,
      text: "法院认为：经营者未能充分证明损害完全由消费者不当使用导致，应结合产品缺陷与因果关系承担相应责任。",
    },
    {
      chunk_id: "case-001-c3",
      chunk_type: "judgment_result",
      source_anchors: [
        sourceAnchor("case-001", "case-001-c3", "detail_chunk", "judgment_result"),
      ],
      start_offset: 181,
      end_offset: 240,
      text: "判决结果：经营者在责任范围内赔偿消费者合理损失。",
    },
  ],
};

beforeEach(() => {
  vi.restoreAllMocks();
  vi.stubEnv("VITE_ENABLE_M1_M5_ACCEPTANCE", "false");
  vi.stubEnv("VITE_ENABLE_EXPANDED_SEARCH", "true");
});

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

describe("SearchPage", () => {
  it("submits the query from route state, shows skeleton, then renders results", async () => {
    let resolveSearch: (() => void) | undefined;
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);

      if (url.includes("/api/events")) {
        return Promise.resolve({
          ok: true,
          status: 202,
          json: async () => ({ accepted: true }),
        });
      }

      return new Promise((resolve) => {
        resolveSearch = () =>
          resolve({
            ok: true,
            status: 200,
            json: async () => baseResponse,
          });
      });
    });
    vi.stubGlobal("fetch", fetchMock);

    renderSearchPage({ query: validQuery, inputLength: Array.from(validQuery).length });

    expect(screen.getByLabelText("案情描述")).toHaveValue(validQuery);
    expect(await screen.findByLabelText("正在理解案情")).toBeInTheDocument();

    resolveSearch?.();
    await screen.findByText("找到 1 条可复核案例");
    expect(screen.getByText("事实相似度 87%")).toBeInTheDocument();
    expect(screen.getByText("产品责任纠纷判决书")).toBeInTheDocument();
    expect(screen.getByText("来源 case-001-c1")).toBeInTheDocument();
    expect(screen.getByText("按事实相似度优先排序。分数只表示检索相关度，不代表案件结果或相关案例完整范围。")).toBeInTheDocument();
  });

  it("auto-submits the route-state query under React StrictMode", async () => {
    const fetchMock = mockFetchResponse(baseResponse);

    renderSearchPage(
      { query: validQuery, inputLength: Array.from(validQuery).length },
      { strictMode: true }
    );

    await screen.findByText("找到 1 条可复核案例");
    expect(screen.getByLabelText("案情描述")).toHaveValue(validQuery);
    expect(
      fetchMock.mock.calls.filter(([input]) => String(input) === "/api/search")
    ).toHaveLength(1);
  });

  it("renders the result card core fields and source-backed highlight content", async () => {
    mockFetchResponse(baseResponse);

    renderSearchPage({ query: validQuery });

    await screen.findByText("产品责任纠纷判决书");

    const detailButton = screen.getByRole("button", {
      name: /查看案例详情：产品责任纠纷判决书/,
    });
    const card = detailButton.closest("article");
    expect(card).not.toBeNull();
    const scoped = within(card as HTMLElement);

    expect(scoped.getByRole("heading", { name: "产品责任纠纷判决书" })).toBeInTheDocument();
    expect(scoped.getByText("（2025）测01民初1号")).toBeInTheDocument();
    expect(scoped.getByText("测试人民法院")).toBeInTheDocument();
    expect(scoped.getByText("一审")).toBeInTheDocument();
    expect(scoped.getByText("产品责任纠纷")).toBeInTheDocument();
    expect(scoped.getByText("2025-05-01")).toBeInTheDocument();
    expect(scoped.getByText("事实摘要")).toBeInTheDocument();
    expect(
      scoped.getByText("消费者主张产品存在缺陷并造成损害，经营者抗辩称由不当使用导致。")
    ).toBeInTheDocument();
    expect(scoped.getByText("高亮事实片段")).toBeInTheDocument();
    expect(
      scoped.getByText("产品是否存在缺陷、经营者是否应承担赔偿责任")
    ).toBeInTheDocument();
    expect(scoped.getByText("case-001-c1")).toBeInTheDocument();
    expect(scoped.getByText("向量召回")).toBeInTheDocument();
    expect(
      within(scoped.getByLabelText("事实相似度 87%")).getByText("仅代表检索相关度")
    ).toBeInTheDocument();
  });

  it("shows degraded reasons without claiming semantic recall is available", async () => {
    mockFetchResponse({
      ...baseResponse,
      degraded: true,
      degraded_reasons: ["CHROMA_QUERY_FAILED", "BM25_FALLBACK_USED"],
      results: [
        {
          ...baseResponse.results[0],
          retrieval_source: ["bm25_fallback"],
          fallback_score: 0.76,
          vector_score: null,
        },
      ],
    });

    renderSearchPage({ query: validQuery });

    await screen.findByText("已使用基础检索");
    expect(screen.getByText("向量召回异常，已回退到基础检索。")).toBeInTheDocument();
    expect(screen.getByText("已使用基础关键词检索策略。")).toBeInTheDocument();
    expect(screen.getAllByText("基础检索").length).toBeGreaterThan(0);
  });

  it("renders embedding and Chroma timeout degradation as readable fallback state", async () => {
    mockFetchResponse({
      ...baseResponse,
      degraded: true,
      degraded_reasons: ["EMBEDDING_TIMEOUT", "CHROMA_QUERY_TIMEOUT", "BM25_FALLBACK_USED"],
      results: [
        {
          ...baseResponse.results[0],
          retrieval_source: ["bm25_fallback"],
          fallback_score: 0.76,
          vector_score: null,
        },
      ],
    });

    renderSearchPage({ query: validQuery });

    await screen.findByText("已使用基础检索");
    expect(screen.getByText("向量生成超时，已回退到基础检索。")).toBeInTheDocument();
    expect(screen.getByText("向量召回超时，已回退到基础检索。")).toBeInTheDocument();
    expect(screen.getByText("已使用基础关键词检索策略。")).toBeInTheDocument();
  });

  it("keeps input and offers retry for zero results", async () => {
    const fetchMock = mockFetchResponse({
      ...baseResponse,
      query_session_id: "qs_zero",
      results: [],
      candidates: [],
    });

    renderSearchPage({ query: validQuery });

    await screen.findByText("未找到足够匹配的案例");
    expect(screen.getByLabelText("案情描述")).toHaveValue(validQuery);
    expect(screen.getByRole("button", { name: "扩大复核范围" })).toBeInTheDocument();
    expect(
      screen.getByText("可以尝试补充案件经过、损害结果、争议焦点，或简化为最核心的事实动作。")
    ).toBeInTheDocument();
    expect(screen.getByText("查看示例案情")).toBeInTheDocument();
    await waitFor(() =>
      expect(getAnalyticsEvents(fetchMock).map((event) => event.event_name)).toContain(
        "search_zero_result"
      )
    );
    const zeroResultEvent = getAnalyticsEvents(fetchMock).find(
      (event) => event.event_name === "search_zero_result"
    );
    expect(zeroResultEvent).toMatchObject({
      query_session_id: "qs_zero",
      metadata: {
        input_length: Array.from(validQuery).length,
        fallback_available: true,
      },
    });
  });

  it("requests expanded candidates when primary results are sparse", async () => {
    let resolveExpand: (() => void) | undefined;
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);

      if (url.includes("/api/search/expand")) {
        return new Promise((resolve) => {
          resolveExpand = () =>
            resolve({
              ok: true,
              status: 200,
              json: async () => expandedResponse,
            });
        });
      }

      return Promise.resolve({
        ok: true,
        status: 200,
        json: async () => baseResponse,
      });
    });
    vi.stubGlobal("fetch", fetchMock);

    renderSearchPage({ query: validQuery });

    const expandButton = await screen.findByRole("button", {
      name: "扩大复核范围",
    });
    fireEvent.click(expandButton);

    expect(await screen.findByLabelText("补充候选加载中")).toBeInTheDocument();

    const expandCall = fetchMock.mock.calls.find(([input]) =>
      String(input).includes("/api/search/expand")
    );
    expect(expandCall).toBeTruthy();
    const requestInit = expandCall?.[1] as RequestInit;
    expect(requestInit.method).toBe("POST");
    expect(JSON.parse(String(requestInit.body))).toEqual({
      mode: "expand",
      limit: 10,
      query: validQuery,
    });

    resolveExpand?.();

    await screen.findByText("可能相关产品责任候选");
    expect(screen.getAllByText("部分相关，仅供复核").length).toBeGreaterThan(0);
    expect(screen.getByText("低置信候选")).toBeInTheDocument();
    expect(screen.getByText("补充候选降级原因")).toBeInTheDocument();
    expect(screen.getByText("已使用基础关键词检索策略。")).toBeInTheDocument();
  });

  it("renders standard low-confidence candidates in a separate panel", async () => {
    vi.stubEnv("VITE_ENABLE_EXPANDED_SEARCH", "false");
    const fetchMock = mockFetchResponse({
      ...baseResponse,
      coverage: {
        ...baseResponse.coverage,
        total_candidate_count: 2,
      },
      low_confidence_candidates: [lowConfidenceCandidate],
    });

    renderSearchPage({ query: validQuery });

    await screen.findByText("找到 1 条可复核案例");
    const lowConfidenceList = await screen.findByLabelText("低置信度候选列表");
    expect(within(lowConfidenceList).getByText("可能相关产品责任候选")).toBeInTheDocument();
    expect(within(lowConfidenceList).getByText("候选 #1")).toBeInTheDocument();
    expect(screen.getAllByText("部分相关，仅供复核").length).toBeGreaterThan(0);
    expect(screen.getByText("分数区间较低")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "扩大复核范围" })
    ).not.toBeInTheDocument();
    expect(
      fetchMock.mock.calls.some(([input]) =>
        String(input).includes("/api/search/expand")
      )
    ).toBe(false);
  });

  it("renders sourced risk hints and opens the source entry", async () => {
    mockFetchRoutes({
      searchResponse: {
        ...baseResponse,
        risk_hints: [
          {
            risk_type: "low_confidence_candidate",
            source_anchors: [sourceAnchor("case-001", "case-001-c1", "risk_hint")],
            confidence_level: "low",
            confidence_reasons: ["LOW_SCORE_BAND"],
            reason_code: "LOW_CONFIDENCE_CANDIDATE_REVIEW",
            review_note: "SHOULD_NOT_BE_USED_AS_DISPLAY_BODY",
          },
        ],
      },
      detailResponse: baseCaseDetail,
    });

    renderSearchPage({ query: validQuery });

    await screen.findByText("复核风险提示");
    expect(screen.getByText("供复核")).toBeInTheDocument();
    expect(
      screen.getByText("以下仅为有来源锚点的复核线索，不影响主结果排序。")
    ).toBeInTheDocument();
    expect(screen.getByText("低置信度候选")).toBeInTheDocument();
    expect(screen.getByText("LOW_CONFIDENCE_CANDIDATE_REVIEW")).toBeInTheDocument();
    expect(screen.getByText(/source_chunk_id: case-001-c1/)).toBeInTheDocument();
    expect(document.body.textContent).not.toContain("SHOULD_NOT_BE_USED_AS_DISPLAY_BODY");

    fireEvent.click(screen.getByRole("button", { name: "查看来源" }));

    const dialog = await screen.findByRole("dialog", {
      name: /产品责任纠纷判决书/,
    });
    expect(dialog).toBeInTheDocument();
    expect(screen.getAllByText(/source_chunk_id: case-001-c1/).length).toBeGreaterThan(0);
  });

  it("does not render risk hints without source anchors", async () => {
    mockFetchResponse({
      ...baseResponse,
      risk_hints: [
        {
          risk_type: "low_confidence_candidate",
          source_anchors: [],
          confidence_level: "low",
          confidence_reasons: ["LOW_SCORE_BAND"],
          reason_code: "LOW_CONFIDENCE_CANDIDATE_REVIEW",
        },
      ],
    });

    renderSearchPage({ query: validQuery });

    await screen.findByText("找到 1 条可复核案例");
    expect(screen.queryByText("复核风险提示")).not.toBeInTheDocument();
    expect(screen.queryByText("LOW_CONFIDENCE_CANDIDATE_REVIEW")).not.toBeInTheDocument();
  });

  it("marks primary and low-confidence results with sanitized feedback events", async () => {
    vi.stubEnv("VITE_ENABLE_EXPANDED_SEARCH", "false");
    const setItemSpy = vi.spyOn(Storage.prototype, "setItem");
    const fetchMock = mockFetchResponse({
      ...baseResponse,
      low_confidence_candidates: [lowConfidenceCandidate],
    });

    renderSearchPage({ query: validQuery });

    await screen.findByText("找到 1 条可复核案例");
    const primaryCard = screen
      .getByRole("button", { name: /查看案例详情：产品责任纠纷判决书/ })
      .closest("article");
    expect(primaryCard).not.toBeNull();
    const primaryScope = within(primaryCard as HTMLElement);

    fireEvent.click(
      primaryScope.getByRole("button", { name: /标记为相关：产品责任纠纷判决书/ })
    );

    await waitFor(() => expect(getFeedbackEvents(fetchMock)).toHaveLength(1));
    expect(primaryScope.getByText("已标记相关")).toBeInTheDocument();

    fireEvent.click(
      primaryScope.getByRole("button", { name: /撤销相关标记：产品责任纠纷判决书/ })
    );

    await waitFor(() => expect(getFeedbackEvents(fetchMock)).toHaveLength(2));

    fireEvent.click(
      primaryScope.getByRole("button", { name: /标记为不相关：产品责任纠纷判决书/ })
    );

    await waitFor(() => expect(getFeedbackEvents(fetchMock)).toHaveLength(3));
    expect(primaryScope.getByText("已标记不相关")).toBeInTheDocument();

    const lowConfidenceList = await screen.findByLabelText("低置信度候选列表");
    fireEvent.click(
      within(lowConfidenceList).getByRole("button", {
        name: /标记为不相关：可能相关产品责任候选/,
      })
    );

    await waitFor(() => expect(getFeedbackEvents(fetchMock)).toHaveLength(4));

    const feedbackEvents = getFeedbackEvents(fetchMock);
    expect(feedbackEvents.map((event) => event.feedback_value)).toEqual([
      "relevant",
      "cleared",
      "not_relevant",
      "not_relevant",
    ]);
    expect(feedbackEvents[0]).toMatchObject({
      event_type: "result_feedback",
      rank: 1,
      feedback_value: "relevant",
      search_mode: "standard",
      confidence_level: "high",
    });
    expect(feedbackEvents[3]).toMatchObject({
      event_type: "result_feedback",
      rank: 1,
      feedback_value: "not_relevant",
      search_mode: "standard",
      confidence_level: "low",
    });
    feedbackEvents.forEach((event) => {
      expect(Object.keys(event)).toEqual([
        "event_type",
        "session_hash",
        "query_hash",
        "case_id_hash",
        "rank",
        "feedback_value",
        "search_mode",
        "confidence_level",
      ]);
      expect(event.session_hash).toMatch(/^(sha256_[a-f0-9]{24}|fnv1a_[a-f0-9]{8})$/);
      expect(event.query_hash).toMatch(/^(sha256_[a-f0-9]{24}|fnv1a_[a-f0-9]{8})$/);
      expect(event.case_id_hash).toMatch(/^(sha256_[a-f0-9]{24}|fnv1a_[a-f0-9]{8})$/);
      const serialized = JSON.stringify(event);
      expect(serialized).not.toContain(validQuery);
      expect(serialized).not.toContain("case-001");
      expect(serialized).not.toContain("case-expand-001");
      expect(serialized).not.toContain("产品缺陷争议片段");
      expect(serialized).not.toContain("损害原因和责任争议部分相关");
      ["query", "raw_query", "case_text", "candidate_body", "chunk_body", "text", "reason"].forEach(
        (field) => expect(event).not.toHaveProperty(field)
      );
    });
    expect(setItemSpy).not.toHaveBeenCalled();
  });

  it("emits privacy-safe analytics for search, render, click, detail, and expand", async () => {
    const fetchMock = mockFetchRoutes({
      searchResponse: baseResponse,
      expandResponse: expandedResponse,
      detailResponse: baseCaseDetail,
    });
    const consoleLogSpy = vi.spyOn(console, "log").mockImplementation(() => undefined);
    const consoleErrorSpy = vi
      .spyOn(console, "error")
      .mockImplementation(() => undefined);

    renderSearchPage();

    fireEvent.change(screen.getByLabelText("案情描述"), {
      target: { value: validQuery },
    });
    fireEvent.click(screen.getByRole("button", { name: "重新检索" }));

    await screen.findByText("找到 1 条可复核案例");
    fireEvent.click(screen.getByRole("button", { name: "扩大复核范围" }));
    fireEvent.click(
      screen.getByRole("button", {
        name: /查看案例详情：产品责任纠纷判决书/,
      })
    );
    await screen.findByRole("dialog", { name: /产品责任纠纷判决书/ });

    await waitFor(() => {
      expect(getAnalyticsEvents(fetchMock).map((event) => event.event_name)).toEqual(
        expect.arrayContaining([
          "search_submit",
          "search_result_render",
          "extended_search_trigger",
          "result_card_click",
          "case_detail_view",
        ])
      );
    });

    const analyticsEvents = getAnalyticsEvents(fetchMock);
    const eventNames = analyticsEvents.map((event) => event.event_name);
    expect(eventNames).toEqual(
      expect.arrayContaining([
        "search_submit",
        "search_result_render",
        "extended_search_trigger",
        "result_card_click",
        "case_detail_view",
      ])
    );
    expect(
      analyticsEvents
        .filter((event) => event.event_name !== "search_submit")
        .every((event) => typeof event.query_session_id === "string")
    ).toBe(true);
    expect(
      analyticsEvents.find((event) => event.event_name === "search_result_render")
    ).toMatchObject({
      query_session_id: "qs_test_001",
      metadata: {
        result_count: 1,
        total_duration_ms: 130,
        degraded: false,
        degraded_reason_count: 0,
      },
    });
    expect(
      analyticsEvents.find((event) => event.event_name === "extended_search_trigger")
    ).toMatchObject({
      query_session_id: "qs_test_001",
      metadata: {
        main_result_count: 1,
      },
    });
    const clickEvent = analyticsEvents.find(
      (event) => event.event_name === "result_card_click"
    );
    const detailEvent = analyticsEvents.find(
      (event) => event.event_name === "case_detail_view"
    );
    expect(clickEvent?.metadata.case_id_hash).toMatch(
      /^(sha256_[a-f0-9]{24}|fnv1a_[a-f0-9]{8})$/
    );
    expect(detailEvent?.metadata.case_id_hash).toBe(
      clickEvent?.metadata.case_id_hash
    );
    expect(JSON.stringify(analyticsEvents)).not.toContain(validQuery);
    expect(JSON.stringify(analyticsEvents)).not.toContain("case-001");
    expect(JSON.stringify(analyticsEvents)).not.toContain(baseCaseDetail.chunks[0].text);
    expect(consoleLogSpy).not.toHaveBeenCalled();
    expect(consoleErrorSpy).not.toHaveBeenCalled();
  });

  it("emits search_refine with the previous session and no raw query", async () => {
    const fetchMock = mockFetchRoutes({
      searchResponse: baseResponse,
      detailResponse: baseCaseDetail,
    });
    const refinedQuery = `${validQuery} 补充说明：商家曾承诺免费维修但后来拒绝履行。`;

    renderSearchPage({ query: validQuery });

    await screen.findByText("找到 1 条可复核案例");
    fireEvent.change(screen.getByLabelText("案情描述"), {
      target: { value: refinedQuery },
    });
    fireEvent.click(screen.getByRole("button", { name: "重新检索" }));

    await waitFor(() =>
      expect(getAnalyticsEvents(fetchMock).map((event) => event.event_name)).toContain(
        "search_refine"
      )
    );
    const refineEvent = getAnalyticsEvents(fetchMock).find(
      (event) => event.event_name === "search_refine"
    );
    expect(refineEvent).toMatchObject({
      query_session_id: "qs_test_001",
      metadata: {
        refine_count: 1,
        previous_result_count: 1,
        input_length: Array.from(refinedQuery).length,
      },
    });
    expect(JSON.stringify(refineEvent)).not.toContain(refinedQuery);
  });

  it("offers expanded search from zero results and renders candidates separately", async () => {
    mockFetchRoutes({
      searchResponse: {
        ...baseResponse,
        query_session_id: "qs_zero",
        results: [],
        candidates: [],
      },
      expandResponse: expandedResponse,
      detailResponse: baseCaseDetail,
    });

    renderSearchPage({ query: validQuery });

    await screen.findByText("未找到足够匹配的案例");
    fireEvent.click(screen.getByRole("button", { name: "扩大复核范围" }));

    await screen.findByLabelText("低置信度候选列表");
    expect(screen.getByText("可能相关产品责任候选")).toBeInTheDocument();
    expect(screen.getAllByText("部分相关，仅供复核").length).toBeGreaterThan(0);
  });

  it("keeps primary results and input when expanded search fails", async () => {
    mockFetchRoutes({
      searchResponse: baseResponse,
      detailResponse: baseCaseDetail,
      failExpand: true,
    });

    renderSearchPage({ query: validQuery });

    await screen.findByText("产品责任纠纷判决书");
    fireEvent.click(screen.getByRole("button", { name: "扩大复核范围" }));

    await screen.findByText("扩大复核范围失败");
    expect(screen.getByText("产品责任纠纷判决书")).toBeInTheDocument();
    expect(screen.getByLabelText("案情描述")).toHaveValue(validQuery);
    expect(screen.getByText("补充候选暂时不可用。")).toBeInTheDocument();
  });

  it("does not show the expanded search entry when primary results are sufficient", async () => {
    mockFetchResponse(makeSearchResponseWithResultCount(5));

    renderSearchPage({ query: validQuery });

    await screen.findByText("找到 5 条可复核案例");
    expect(screen.queryByText("补充候选")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "扩大复核范围" })
    ).not.toBeInTheDocument();
  });

  it("hides expanded search entries when the rollout flag is disabled", async () => {
    vi.stubEnv("VITE_ENABLE_EXPANDED_SEARCH", "false");
    const fetchMock = mockFetchResponse({
      ...baseResponse,
      query_session_id: "qs_zero",
      results: [],
      candidates: [],
    });

    renderSearchPage({ query: validQuery });

    await screen.findByText("未找到足够匹配的案例");
    expect(screen.queryByRole("button", { name: "扩大复核范围" })).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "扩大复核范围" })
    ).not.toBeInTheDocument();
    expect(
      fetchMock.mock.calls.some(([input]) =>
        String(input).includes("/api/search/expand")
      )
    ).toBe(false);
  });

  it("renders rollback search responses with source snippets and base retrieval scores", async () => {
    vi.stubEnv("VITE_ENABLE_EXPANDED_SEARCH", "false");
    mockFetchResponse({
      ...baseResponse,
      degraded: true,
      degraded_reasons: ["QUERY_REWRITE_DISABLED", "SUMMARY_DISABLED"],
      results: [
        {
          ...baseResponse.results[0],
          final_score: null,
          similarity_score: null,
          retrieval_score: 0.73,
          score_breakdown: {
            score_mode: "base_retrieval",
            weighted_rerank_enabled: false,
          },
          summary: {
            text: "原文片段：经营者未能证明损害完全由消费者不当使用导致。",
            source_chunk_id: "case-001-c1",
            source_case_id: "case-001",
            source_anchors: [sourceAnchor("case-001", "case-001-c1", "summary")],
            method: "source_snippet",
            degraded_reason: "SUMMARY_DISABLED",
          },
          matched_text: "经营者未能证明损害完全由消费者不当使用导致。",
        },
      ],
    });

    renderSearchPage({ query: validQuery });

    await screen.findByText("找到 1 条可复核案例");
    expect(screen.getByText("事实相似度 73%")).toBeInTheDocument();
    expect(
      screen.getByText("原文片段：经营者未能证明损害完全由消费者不当使用导致。")
    ).toBeInTheDocument();
    expect(
      screen.getByText("摘要生成降级：摘要生成已关闭，显示可复核来源片段")
    ).toBeInTheDocument();
    expect(screen.getByText("案情改写未启用，使用原始输入检索。")).toBeInTheDocument();
    expect(screen.getByText("摘要生成已关闭，展示可复核来源片段。")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "扩大复核范围" })
    ).not.toBeInTheDocument();
  });

  it("does not render prohibited absolute-confidence copy", async () => {
    mockFetchResponse(baseResponse);

    renderSearchPage({ query: validQuery });

    await screen.findByText("找到 1 条可复核案例");
    [
      ["已", "查全"].join(""),
      ["保证", "无遗漏"].join(""),
      ["查全", "率"].join(""),
      ["胜诉", "概率"].join(""),
      ["败诉", "概率"].join(""),
      ["法律", "结论"].join(""),
      ["败诉", "风险已确定"].join(""),
      "未证实的数据总量",
      ["扩大", "覆盖"].join(""),
    ].forEach((copy) => {
      expect(document.body.textContent).not.toContain(copy);
    });
  });

  it("renders coverage fields without fabricating unavailable source data", async () => {
    mockFetchResponse({
      ...baseResponse,
      coverage: {
        data_source: "unavailable",
        data_until: "unknown",
        index_version: "unknown",
        total_candidate_count: null,
        search_mode: "standard",
        degraded_reasons: [
          "DATA_SOURCE_UNAVAILABLE",
          "DATA_UNTIL_UNKNOWN",
          "INDEX_VERSION_UNKNOWN",
        ],
      },
    });

    renderSearchPage({ query: validQuery });

    await screen.findByText("当前数据覆盖信息暂不可用，已按本次可用检索结果展示。");
    expect(screen.getByText("来源暂不可用")).toBeInTheDocument();
    expect(screen.getByText("截止日期暂不可用")).toBeInTheDocument();
    expect(screen.getByText("索引版本暂不可用")).toBeInTheDocument();
    expect(screen.getByText("候选数暂不可用")).toBeInTheDocument();
    expect(document.body.textContent).not.toContain(["100", "万"].join(""));
    expect(document.body.textContent).not.toContain(["全部", "案例"].join(""));
  });

  it("keeps input and exposes a retry action when the API fails", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({
        ok: false,
        status: 503,
        json: async () => ({
          error: {
            code: "SEARCH_RETRIEVAL_FAILED",
            message: "检索召回暂时不可用，请稍后重试。",
            query_session_id: "qs_error",
          },
        }),
      })
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => baseResponse,
      });
    vi.stubGlobal("fetch", fetchMock);

    renderSearchPage({ query: validQuery });

    await screen.findByText("检索请求未完成");
    expect(screen.getByText("检索召回暂时不可用，请稍后重试。")).toBeInTheDocument();
    expect(screen.getByLabelText("案情描述")).toHaveValue(validQuery);
    fireEvent.click(screen.getByRole("button", { name: "重试" }));

    await screen.findByText("找到 1 条可复核案例");
    expect(screen.getByLabelText("案情描述")).toHaveValue(validQuery);
    expect(
      fetchMock.mock.calls.filter(([input]) => String(input) === "/api/search")
    ).toHaveLength(2);
  });

  it("keeps input and shows a retryable timeout error when the search request aborts", async () => {
    const fetchMock = vi
      .fn()
      .mockRejectedValueOnce(new DOMException("The request was aborted.", "AbortError"))
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => baseResponse,
      });
    vi.stubGlobal("fetch", fetchMock);

    renderSearchPage({ query: validQuery });

    await screen.findByText("检索请求未完成");
    expect(
      screen.getByText("检索请求超时，请稍后重试；若连续超时，可先关闭查询改写或摘要增强后再试。")
    ).toBeInTheDocument();
    expect(screen.getByLabelText("案情描述")).toHaveValue(validQuery);

    fireEvent.click(screen.getByRole("button", { name: "重试" }));

    await screen.findByText("找到 1 条可复核案例");
    expect(screen.getByLabelText("案情描述")).toHaveValue(validQuery);
    expect(
      fetchMock.mock.calls.filter(([input]) => String(input) === "/api/search")
    ).toHaveLength(2);
  });

  it("does not let analytics network failures interrupt search rendering", async () => {
    const analyticsFailure = new Error("analytics request failed with raw query");
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);

      if (url.includes("/api/events")) {
        return Promise.reject(analyticsFailure);
      }

      return Promise.resolve({
        ok: true,
        status: 200,
        json: async () => baseResponse,
      });
    });
    vi.stubGlobal("fetch", fetchMock);

    renderSearchPage({ query: validQuery });

    await screen.findByText("找到 1 条可复核案例");
    expect(screen.getByText("产品责任纠纷判决书")).toBeInTheDocument();
    expect(screen.queryByText("检索请求未完成")).not.toBeInTheDocument();
    await waitFor(() =>
      expect(fetchMock.mock.calls.some(([input]) => String(input) === "/api/events")).toBe(
        true
      )
    );
  });

  it("can render the explicitly marked frontend mock fixture", async () => {
    renderSearchPage();

    fireEvent.change(screen.getByLabelText("案情描述"), {
      target: { value: validQuery },
    });
    fireEvent.click(screen.getByRole("button", { name: "测试数据" }));

    await screen.findByText("当前使用前端测试数据，所有案例均为非真实样例，仅用于验证页面渲染。");
    expect(screen.getByText("【测试数据】产品缺陷责任纠纷样例（非真实案例）")).toBeInTheDocument();
  });

  it("lazy-loads anchored fact alignment and filters outcome copy", async () => {
    const fetchMock = mockFetchRoutes({
      searchResponse: baseResponse,
      detailResponse: baseCaseDetail,
      factAlignmentResponse: {
        query_session_id: "qs_detail_001",
        case_id: "case-001",
        items: [
          {
            dimension: "行为类型",
            dimension_key: "act_type",
            query_side_signal: "input_signals_dimension",
            case_side_facts: ["案件行为类型：产品缺陷"],
            source_anchors: [
              sourceAnchor("case-001", "case-001-c1", "detail_chunk", "court_found"),
            ],
            match_type: "same_dimension",
            confidence: "medium",
            degrade_reason: null,
          },
          {
            dimension: "损害后果",
            dimension_key: "injury",
            query_side_signal: "input_does_not_mention_dimension",
            case_side_facts: ["损害后果相关事实：损害"],
            source_anchors: [
              sourceAnchor("case-001", "case-001-c2", "detail_chunk", "court_opinion"),
            ],
            match_type: "difference_to_review",
            confidence: "low",
            degrade_reason: null,
          },
          {
            dimension: "应当被过滤",
            dimension_key: "act_type",
            query_side_signal: "input_signals_dimension",
            case_side_facts: ["胜诉概率高，必然支持"],
            source_anchors: [
              sourceAnchor("case-001", "case-001-c1", "detail_chunk", "court_found"),
            ],
            match_type: "same_dimension",
            confidence: "medium",
            degrade_reason: null,
          },
          {
            dimension: "无锚点维度",
            dimension_key: "evidence",
            query_side_signal: "input_signals_dimension",
            case_side_facts: ["证据与举证相关事实：证据"],
            source_anchors: [],
            match_type: "same_dimension",
            confidence: "low",
            degrade_reason: null,
          },
        ],
        generation_status: "generated",
        degrade_reason: null,
        query_signal_present: true,
        timings: baseResponse.timings,
      },
    });

    renderSearchPage({ query: validQuery });

    await screen.findByText("找到 1 条可复核案例");
    fireEvent.click(
      screen.getByRole("button", { name: /查看案例详情：产品责任纠纷判决书/ })
    );
    await screen.findByRole("dialog", { name: /产品责任纠纷判决书/ });

    // Section present, but alignment NOT fetched until user requests it.
    expect(await screen.findByText("相似事实对比")).toBeInTheDocument();
    expect(
      fetchMock.mock.calls.filter(([input]) =>
        String(input).includes("/fact-alignment")
      ).length
    ).toBe(0);

    fireEvent.click(screen.getByRole("button", { name: "加载事实对比" }));

    // Anchored dimensions render; review-clue labels present.
    expect(await screen.findByText("维度：行为类型")).toBeInTheDocument();
    expect(screen.getByText("相同维度")).toBeInTheDocument();
    expect(screen.getByText("需复核差异")).toBeInTheDocument();
    expect(screen.getByText("案件行为类型：产品缺陷")).toBeInTheDocument();

    // POST to fact-alignment endpoint with query_signal in body only.
    const factCall = fetchMock.mock.calls.find(([input]) =>
      String(input).includes("/fact-alignment")
    );
    expect(factCall).toBeTruthy();
    expect(factCall?.[1]).toEqual(
      expect.objectContaining({ method: "POST" })
    );
    expect(String(factCall?.[0])).not.toContain("query_signal");

    // Forbidden outcome copy and unanchored dimension are filtered out.
    expect(screen.queryByText(/必然支持/)).not.toBeInTheDocument();
    expect(screen.queryByText(/胜诉概率/)).not.toBeInTheDocument();
    expect(screen.queryByText("证据与举证相关事实：证据")).not.toBeInTheDocument();
  });

  it("opens the case detail drawer, renders sourced detail, and restores focus on close", async () => {
    const fetchMock = mockFetchRoutes({
      searchResponse: baseResponse,
      detailResponse: baseCaseDetail,
    });

    renderSearchPage({ query: validQuery });

    await screen.findByText("找到 1 条可复核案例");
    const detailButton = screen.getByRole("button", {
      name: /查看案例详情：产品责任纠纷判决书/,
    });

    fireEvent.click(detailButton);

    expect(screen.getByLabelText("案例详情加载中")).toBeInTheDocument();

    const dialog = await screen.findByRole("dialog", {
      name: /产品责任纠纷判决书/,
    });
    expect(dialog).toBeInTheDocument();
    expect(dialog).toHaveClass("w-full");
    expect(screen.getByRole("button", { name: "关闭案例详情抽屉" })).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "关闭案例详情抽屉" })).toHaveFocus()
    );
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/cases/case-001",
      expect.objectContaining({ method: "GET" })
    );
    expect(screen.getByText("完整摘要")).toBeInTheDocument();
    expect(screen.getByText("裁判要旨摘要")).toBeInTheDocument();
    expect(
      screen.getByText("法院围绕产品缺陷、损害原因和经营者举证情况整理裁判说理，供阅读复核。")
    ).toBeInTheDocument();
    expect(screen.getByText("争议焦点与关键要素")).toBeInTheDocument();
    expect(screen.getByText("复核线索与阅读定位，均需回到来源片段确认")).toBeInTheDocument();
    expect(screen.getByText("围绕产品缺陷、损害原因的争议复核")).toBeInTheDocument();
    expect(screen.getByText("关键要素：产品缺陷、因果关系相关说理")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "查看来源片段" }).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/source_chunk_id: case-001-c1/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/source_chunk_id: case-001-c2/).length).toBeGreaterThan(0);
    expect(screen.getByText("打开原文")).toHaveAttribute(
      "href",
      "https://example.test/case-001"
    );

    fireEvent.click(screen.getByRole("button", { name: "关闭案例详情抽屉" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    await waitFor(() => expect(detailButton).toHaveFocus());
  });

  it("keeps results visible when detail fails and retries the detail request", async () => {
    mockFetchRoutes({
      searchResponse: baseResponse,
      detailResponse: baseCaseDetail,
      failFirstDetail: true,
    });

    renderSearchPage({ query: validQuery });

    await screen.findByText("找到 1 条可复核案例");
    fireEvent.click(
      screen.getByRole("button", {
        name: /查看案例详情：产品责任纠纷判决书/,
      })
    );

    await screen.findByText("案例详情加载失败");
    expect(screen.getAllByText("产品责任纠纷判决书").length).toBeGreaterThan(0);
    expect(screen.getByText("详情服务暂时不可用，请稍后重试。")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "重试" }));

    expect(
      (
        await screen.findAllByText(
          "法院认为：经营者未能充分证明损害完全由消费者不当使用导致，应结合产品缺陷与因果关系承担相应责任。"
        )
      ).length
    ).toBeGreaterThan(0);
  });

  it("closes the detail drawer with Escape", async () => {
    mockFetchRoutes({
      searchResponse: baseResponse,
      detailResponse: baseCaseDetail,
    });

    renderSearchPage({ query: validQuery });

    await screen.findByText("找到 1 条可复核案例");
    fireEvent.click(
      screen.getByRole("button", {
        name: /查看案例详情：产品责任纠纷判决书/,
      })
    );

    await screen.findByRole("dialog", { name: /产品责任纠纷判决书/ });
    fireEvent.keyDown(document, { key: "Escape" });

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("keeps the detail close button keyboard-reachable in the mobile drawer layout", async () => {
    mockFetchRoutes({
      searchResponse: baseResponse,
      detailResponse: baseCaseDetail,
    });

    renderSearchPage({ query: validQuery });

    await screen.findByText("找到 1 条可复核案例");
    fireEvent.click(
      screen.getByRole("button", {
        name: /查看案例详情：产品责任纠纷判决书/,
      })
    );

    const closeButton = await screen.findByRole("button", {
      name: "关闭案例详情抽屉",
    });
    expect(closeButton).toHaveFocus();
    expect(closeButton).toHaveClass("h-10");
  });

  it("uses overflow-safe layout classes for the 375px mobile viewport", async () => {
    mockFetchResponse(baseResponse);

    renderSearchPage({ query: validQuery });

    await screen.findByText("找到 1 条可复核案例");
    const main = document.querySelector("main");
    const resultsSection = screen.getByLabelText("搜索结果列表");
    const card = screen
      .getByRole("button", { name: /查看案例详情：产品责任纠纷判决书/ })
      .closest("article");
    const highlightSource = screen.getByText("case-001-c1");

    expect(main).toHaveClass("min-h-[100dvh]");
    expect(resultsSection.parentElement).toHaveClass("min-w-0");
    expect(card).toHaveClass("cursor-pointer");
    expect(card).toHaveClass("rounded-[8px]");
    expect(highlightSource).toHaveClass("whitespace-nowrap");
    expect(highlightSource).toHaveClass("text-ellipsis");
  });

  it("does not display summary text that lacks a source anchor", async () => {
    mockFetchRoutes({
      searchResponse: {
        ...baseResponse,
        results: [
          {
            ...baseResponse.results[0],
            source_chunk_ids: [],
            source_anchors: [],
            summary: {
              text: "无来源的生成摘要不应展示",
              method: "llm_deepseek",
            },
            highlights: [
              {
                text: "无来源的高亮不应展示",
                source_chunk_id: "",
              },
            ],
            matched_text: "可核验原文片段",
          },
        ],
      },
      detailResponse: {
        ...baseCaseDetail,
        holding_summary: {
          summary_items: [
            {
              text: "无来源的裁判要旨摘要不应展示",
              source_anchors: [],
              confidence: "medium",
            },
          ],
          source_anchors: [],
          confidence: "low",
          generation_status: "generated",
          degrade_reason: null,
        },
        chunks: [
          {
            chunk_id: "case-001-c2",
            chunk_type: "court_opinion",
            text: "法院认为：该片段有来源，可用于裁判要旨核验。",
          },
        ],
      },
    });

    renderSearchPage({ query: validQuery });

    await screen.findByText("暂无可核验摘要来源，未展示 AI 摘要。");
    expect(screen.queryByText("可核验原文片段")).not.toBeInTheDocument();
    expect(screen.queryByText("无来源的高亮不应展示")).not.toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", {
        name: /查看案例详情：产品责任纠纷判决书/,
      })
    );

    await screen.findByText("暂无可核验摘要来源，未展示 AI 摘要。");
    expect(screen.queryByText("无来源的生成摘要不应展示")).not.toBeInTheDocument();
    expect(screen.queryByText("无来源的裁判要旨摘要不应展示")).not.toBeInTheDocument();
    expect(screen.queryByText("围绕产品缺陷、损害原因的争议复核")).not.toBeInTheDocument();
    expect(screen.queryByText("关键要素：产品缺陷、因果关系相关说理")).not.toBeInTheDocument();
  });

  it("hides reading navigation items with forbidden outcome copy or categories", async () => {
    mockFetchRoutes({
      searchResponse: baseResponse,
      detailResponse: {
        ...baseCaseDetail,
        issue_focus: {
          items: [
            {
              label: "胜诉概率不应展示",
              category: "争议焦点",
              source_anchors: [
                sourceAnchor("case-001", "case-001-c1", "detail_chunk", "court_found"),
              ],
              confidence: "medium",
              degrade_reason: null,
            },
          ],
          source_anchors: [
            sourceAnchor("case-001", "case-001-c1", "detail_chunk", "court_found"),
          ],
          generation_status: "generated",
          degrade_reason: null,
        },
        key_elements: {
          items: [
            {
              label: "禁止类别不应展示",
              category: "胜诉或败诉倾向",
              source_anchors: [
                sourceAnchor("case-001", "case-001-c2", "detail_chunk", "court_opinion"),
              ],
              confidence: "medium",
              degrade_reason: null,
            },
          ],
          source_anchors: [
            sourceAnchor("case-001", "case-001-c2", "detail_chunk", "court_opinion"),
          ],
          generation_status: "generated",
          degrade_reason: null,
        },
      },
    });

    renderSearchPage({ query: validQuery });

    await screen.findByText("找到 1 条可复核案例");
    fireEvent.click(
      screen.getByRole("button", {
        name: /查看案例详情：产品责任纠纷判决书/,
      })
    );

    await screen.findByText("争议焦点与关键要素");
    expect(screen.queryByText("胜诉概率不应展示")).not.toBeInTheDocument();
    expect(screen.queryByText("禁止类别不应展示")).not.toBeInTheDocument();
    expect(
      screen.getByText("暂无可核验争议焦点或关键要素，已保留来源片段入口供复核。")
    ).toBeInTheDocument();
  });

  it("degrades holding summary on model failure and keeps source entries available", async () => {
    mockFetchRoutes({
      searchResponse: baseResponse,
      detailResponse: {
        ...baseCaseDetail,
        holding_summary: {
          summary_items: [],
          source_anchors: [],
          confidence: "low",
          generation_status: "degraded",
          degrade_reason: "model_failed",
        },
      },
    });

    renderSearchPage({ query: validQuery });

    await screen.findByText("找到 1 条可复核案例");
    fireEvent.click(
      screen.getByRole("button", {
        name: /查看案例详情：产品责任纠纷判决书/,
      })
    );

    await screen.findByText("摘要生成暂不可用，已保留来源片段入口供复核。");
    expect(
      screen.queryByText("法院围绕产品缺陷、损害原因和经营者举证情况整理裁判说理，供阅读复核。")
    ).not.toBeInTheDocument();
    expect(screen.getAllByText(/source_chunk_id: case-001-c2/).length).toBeGreaterThan(0);
  });
});

function sourceAnchor(
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
    source_ref: "frontend_test_fixture",
  };
}

function renderSearchPage(
  state?: { query?: string; inputLength?: number },
  options: { strictMode?: boolean } = {}
) {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });

  const page = (
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[{ pathname: "/search", state }]}>
        <Routes>
          <Route path="/search" element={<SearchPage />} />
          <Route path="/" element={<div>home</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );

  return render(options.strictMode ? <StrictMode>{page}</StrictMode> : page);
}

function mockFetchResponse(response: SearchResponse) {
  const fetchMock = vi.fn().mockResolvedValue({
    ok: true,
    status: 200,
    json: async () => response,
  });

  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function mockFetchRoutes({
  searchResponse,
  expandResponse,
  detailResponse,
  factAlignmentResponse,
  failFactAlignment = false,
  failFirstDetail = false,
  failExpand = false,
}: {
  searchResponse: SearchResponse;
  expandResponse?: SearchResponse;
  detailResponse: CaseDetailResponse;
  factAlignmentResponse?: Record<string, unknown>;
  failFactAlignment?: boolean;
  failFirstDetail?: boolean;
  failExpand?: boolean;
}) {
  let detailCalls = 0;
  const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) => {
    const url = String(input);

    if (url.includes("/fact-alignment")) {
      if (failFactAlignment) {
        return Promise.resolve({
          ok: false,
          status: 503,
          json: async () => ({
            error: {
              code: "FACT_ALIGNMENT_UNAVAILABLE",
              message: "事实对比暂时不可用。",
              query_session_id: "qs_fact_align_error",
            },
          }),
        });
      }
      return Promise.resolve({
        ok: true,
        status: 200,
        json: async () =>
          factAlignmentResponse || {
            query_session_id: "qs_detail_001",
            case_id: "case-001",
            items: [],
            generation_status: "degraded",
            degrade_reason: "insufficient_source",
            query_signal_present: false,
            timings: searchResponse.timings,
          },
      });
    }

    if (url.includes("/api/search/expand")) {
      if (failExpand) {
        return Promise.resolve({
          ok: false,
          status: 503,
          json: async () => ({
            error: {
              code: "SEARCH_EXPAND_FAILED",
              message: "补充候选暂时不可用。",
              query_session_id: "qs_expand_error",
            },
          }),
        });
      }

      return Promise.resolve({
        ok: true,
        status: 200,
        json: async () => expandResponse || searchResponse,
      });
    }

    if (url.includes("/api/cases/")) {
      detailCalls += 1;

      if (failFirstDetail && detailCalls === 1) {
        return Promise.resolve({
          ok: false,
          status: 503,
          json: async () => ({
            error: {
              code: "CASE_DETAIL_UNAVAILABLE",
              message: "详情服务暂时不可用，请稍后重试。",
              query_session_id: "qs_detail_error",
            },
          }),
        });
      }

      return Promise.resolve({
        ok: true,
        status: 200,
        json: async () => detailResponse,
      });
    }

    return Promise.resolve({
      ok: true,
      status: 200,
      json: async () => searchResponse,
    });
  });

  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function makeSearchResponseWithResultCount(count: number): SearchResponse {
  const results = Array.from({ length: count }, (_, index) => {
    const caseId = `case-full-${index + 1}`;
    const chunkId = `${caseId}-c1`;
    return {
      ...baseResponse.results[0],
      case_id: caseId,
      chunk_id: chunkId,
      top_chunk_id: chunkId,
      source_chunk_ids: [chunkId],
      source_anchors: [sourceAnchor(caseId, chunkId, "result")],
      hit_chunk_ids: [chunkId],
      title: `主结果案例 ${index + 1}`,
      summary: {
        ...baseResponse.results[0].summary,
        source_chunk_id: chunkId,
        source_case_id: caseId,
        source_anchors: [sourceAnchor(caseId, chunkId, "summary")],
      },
      highlights: [
        {
          ...baseResponse.results[0].highlights[0],
          source_chunk_id: chunkId,
          source_anchors: [sourceAnchor(caseId, chunkId, "highlight")],
        },
      ],
    };
  });

  return {
    ...baseResponse,
    coverage: {
      ...baseResponse.coverage,
      total_candidate_count: count,
    },
    results,
    candidates: results,
  };
}

function getAnalyticsEvents(fetchMock: ReturnType<typeof vi.fn>) {
  return fetchMock.mock.calls
    .filter(([input]) => String(input) === "/api/events")
    .map(([, init]) => JSON.parse(String((init as RequestInit).body)));
}

function getFeedbackEvents(fetchMock: ReturnType<typeof vi.fn>) {
  return fetchMock.mock.calls
    .filter(([input]) => String(input) === "/api/feedback")
    .map(([, init]) => JSON.parse(String((init as RequestInit).body)));
}
