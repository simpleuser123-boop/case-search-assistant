import type {
  CaseDetailResponse,
  FactAlignmentItem,
  FactAlignmentResponse,
  SourceAnchor,
} from "../types/search";

const mockTimings = {
  rewrite_duration_ms: 0,
  embedding_duration_ms: 0,
  retrieval_duration_ms: 42,
  rerank_duration_ms: 0,
  summary_duration_ms: 0,
  total_duration_ms: 42,
};

export const MOCK_CASE_DETAILS: Record<string, CaseDetailResponse> = {
  "mock-case-001-non-real": {
    query_session_id: "qs_mock_frontend_only",
    case_id: "mock-case-001-non-real",
    title: "【测试数据】产品缺陷责任纠纷样例（非真实案例）",
    case_no: "TEST-2026-MOCK-001（非真实案号）",
    court: "测试法院（非真实）",
    court_level: "基层法院",
    trial_level: "一审",
    case_cause: "产品责任纠纷",
    judgment_date: "2026-01-15",
    region: "测试地区",
    source_url: "",
    source_name: "frontend_mock_fixture",
    issue_focus: {
      items: [
        {
          label: "围绕产品缺陷、损害原因的争议复核",
          category: "争议焦点",
          source_anchors: [
            mockDetailAnchor("mock-case-001-non-real", "mock-case-001-c1", "court_found"),
          ],
          confidence: "medium",
          degrade_reason: null,
        },
      ],
      source_anchors: [
        mockDetailAnchor("mock-case-001-non-real", "mock-case-001-c1", "court_found"),
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
            mockDetailAnchor("mock-case-001-non-real", "mock-case-001-c2", "court_opinion"),
          ],
          confidence: "medium",
          degrade_reason: null,
        },
      ],
      source_anchors: [
        mockDetailAnchor("mock-case-001-non-real", "mock-case-001-c2", "court_opinion"),
      ],
      generation_status: "generated",
      degrade_reason: null,
    },
    chunks: [
      {
        chunk_id: "mock-case-001-c1",
        chunk_type: "court_found",
        source_anchors: [
          mockDetailAnchor("mock-case-001-non-real", "mock-case-001-c1", "court_found"),
        ],
        start_offset: 0,
        end_offset: 120,
        text: "前端测试数据片段：消费者主张小家电存在安全缺陷并造成损害，经营者抗辩称损害由不当使用导致。该片段用于验证详情抽屉的来源核验能力。",
      },
      {
        chunk_id: "mock-case-001-c2",
        chunk_type: "court_opinion",
        source_anchors: [
          mockDetailAnchor("mock-case-001-non-real", "mock-case-001-c2", "court_opinion"),
        ],
        start_offset: 121,
        end_offset: 260,
        text: "前端测试数据片段：法院围绕产品是否存在缺陷、缺陷与损害之间是否具有因果关系、经营者能否举证免责进行说理。本段为非真实样例。",
      },
      {
        chunk_id: "mock-case-001-c3",
        chunk_type: "judgment_result",
        source_anchors: [
          mockDetailAnchor(
            "mock-case-001-non-real",
            "mock-case-001-c3",
            "judgment_result"
          ),
        ],
        start_offset: 261,
        end_offset: 330,
        text: "前端测试数据片段：判令经营者在责任范围内赔偿合理损失。该裁判结果仅用于界面状态测试。",
      },
    ],
    degraded: false,
    degraded_reasons: [],
    timings: mockTimings,
  },
  "mock-case-002-non-real": {
    query_session_id: "qs_mock_frontend_only",
    case_id: "mock-case-002-non-real",
    title: "【测试数据】合同迟延履行样例（非真实案例）",
    case_no: "TEST-2026-MOCK-002（非真实案号）",
    court: "测试中级法院（非真实）",
    court_level: "中级法院",
    trial_level: "二审",
    case_cause: "买卖合同纠纷",
    judgment_date: "2026-02-20",
    region: "测试地区",
    source_url: "",
    source_name: "frontend_mock_fixture",
    chunks: [
      {
        chunk_id: "mock-case-002-c1",
        chunk_type: "court_found",
        source_anchors: [
          mockDetailAnchor("mock-case-002-non-real", "mock-case-002-c1", "court_found"),
        ],
        start_offset: 0,
        end_offset: 116,
        text: "前端测试数据片段：合同约定分批交付，卖方多次迟延履行，买方主张合同目的已难以实现并要求解除合同。",
      },
      {
        chunk_id: "mock-case-002-c2",
        chunk_type: "court_opinion",
        source_anchors: [
          mockDetailAnchor("mock-case-002-non-real", "mock-case-002-c2", "court_opinion"),
        ],
        start_offset: 117,
        end_offset: 240,
        text: "前端测试数据片段：法院结合迟延时间、催告过程和交易目的判断违约程度。本段仅用于展示可核验来源边界。",
      },
    ],
    degraded: false,
    degraded_reasons: [],
    timings: mockTimings,
  },
  "mock-case-003-non-real": {
    query_session_id: "qs_mock_expand_frontend_only",
    case_id: "mock-case-003-non-real",
    title: "【测试数据】可能相关候选样例（非真实案例）",
    case_no: "TEST-2026-MOCK-003（非真实案号）",
    court: "测试法院（非真实）",
    court_level: "基层法院",
    trial_level: "一审",
    case_cause: "服务合同纠纷",
    judgment_date: "2026-03-12",
    region: "测试地区",
    source_url: "",
    source_name: "frontend_mock_fixture",
    chunks: [
      {
        chunk_id: "mock-case-003-c1",
        chunk_type: "court_found",
        source_anchors: [
          mockDetailAnchor("mock-case-003-non-real", "mock-case-003-c1", "court_found"),
        ],
        start_offset: 0,
        end_offset: 130,
        text: "前端测试数据片段：维修服务后发生安全争议，双方围绕服务瑕疵、损害原因和责任范围发生分歧。本段为低置信度候选详情测试。",
      },
      {
        chunk_id: "mock-case-003-c2",
        chunk_type: "court_opinion",
        source_anchors: [
          mockDetailAnchor("mock-case-003-non-real", "mock-case-003-c2", "court_opinion"),
        ],
        start_offset: 131,
        end_offset: 250,
        text: "前端测试数据片段：法院结合服务记录、损害发生时间和当事人举证情况判断责任范围。该内容为非真实样例。",
      },
    ],
    degraded: false,
    degraded_reasons: [],
    timings: mockTimings,
  },
  "mock-case-004-non-real": {
    query_session_id: "qs_mock_expand_frontend_only",
    case_id: "mock-case-004-non-real",
    title: "【测试数据】损害因果关系候选样例（非真实案例）",
    case_no: "TEST-2026-MOCK-004（非真实案号）",
    court: "测试中级法院（非真实）",
    court_level: "中级法院",
    trial_level: "二审",
    case_cause: "侵权责任纠纷",
    judgment_date: "2026-04-18",
    region: "测试地区",
    source_url: "",
    source_name: "frontend_mock_fixture",
    chunks: [
      {
        chunk_id: "mock-case-004-c1",
        chunk_type: "court_found",
        source_anchors: [
          mockDetailAnchor("mock-case-004-non-real", "mock-case-004-c1", "court_found"),
        ],
        start_offset: 0,
        end_offset: 120,
        text: "前端测试数据片段：当事人围绕损害发生原因、举证责任和赔偿范围争议较大，事实动作与主案情仅部分相近。",
      },
      {
        chunk_id: "mock-case-004-c2",
        chunk_type: "court_opinion",
        source_anchors: [
          mockDetailAnchor("mock-case-004-non-real", "mock-case-004-c2", "court_opinion"),
        ],
        start_offset: 121,
        end_offset: 240,
        text: "前端测试数据片段：法院认为应结合损害原因、证据链完整性和过错程度判断责任分配。本段为非真实样例。",
      },
    ],
    degraded: false,
    degraded_reasons: [],
    timings: mockTimings,
  },
};

function mockDetailAnchor(
  caseId: string,
  chunkId: string,
  chunkType: string
): SourceAnchor {
  return {
    case_id: caseId,
    source_chunk_id: chunkId,
    chunk_type: chunkType,
    anchor_type: "detail_chunk",
    source_ref: "frontend_mock_fixture",
  };
}


const MOCK_FACT_DIMENSIONS: {
  key: string;
  display: string;
  caseLabel: string;
  tokens: string[];
}[] = [
  {
    key: "act_type",
    display: "行为类型",
    caseLabel: "案件行为类型",
    tokens: ["盗窃", "诈骗", "抢劫", "故意伤害", "交通肇事", "危险驾驶", "产品缺陷"],
  },
  {
    key: "amount",
    display: "涉案金额",
    caseLabel: "涉案金额相关事实",
    tokens: ["金额", "数额", "万元", "现金", "财物", "价值", "损失"],
  },
  {
    key: "contract",
    display: "合同与履行",
    caseLabel: "合同履行相关事实",
    tokens: ["合同", "协议", "借款", "履行", "违约", "转账", "还款"],
  },
  {
    key: "injury",
    display: "损害后果",
    caseLabel: "损害后果相关事实",
    tokens: ["受伤", "轻伤", "重伤", "死亡", "损害", "残疾", "鉴定"],
  },
  {
    key: "evidence",
    display: "证据与举证",
    caseLabel: "证据与举证相关事实",
    tokens: ["证据", "举证", "鉴定", "质证", "票据", "转账记录"],
  },
  {
    key: "subjective",
    display: "主观状态",
    caseLabel: "主观状态相关事实",
    tokens: ["故意", "过失", "明知", "自首", "退赔", "谅解"],
  },
];

export function buildMockFactAlignment(
  detail: CaseDetailResponse,
  querySignal: string
): FactAlignmentResponse {
  const queryText = (querySignal || "").trim();
  const queryDimensionKeys = new Set<string>();
  for (const dimension of MOCK_FACT_DIMENSIONS) {
    if (dimension.tokens.some((token) => queryText.includes(token))) {
      queryDimensionKeys.add(dimension.key);
    }
  }
  if (/\d+(?:\.\d+)?\s*(?:万|元|千|百)/.test(queryText)) {
    queryDimensionKeys.add("amount");
  }

  const anchoredChunks = (detail.chunks || []).filter(
    (chunk) =>
      chunk.chunk_id &&
      chunk.text &&
      chunk.text.trim() &&
      ["fact", "court_found", "court_opinion"].includes(chunk.chunk_type || "") &&
      (chunk.source_anchors || []).some(
        (anchor) =>
          anchor.anchor_type === "detail_chunk" &&
          anchor.case_id === detail.case_id &&
          anchor.source_chunk_id === chunk.chunk_id
      )
  );

  const items: FactAlignmentItem[] = [];
  for (const dimension of MOCK_FACT_DIMENSIONS) {
    const anchors: SourceAnchor[] = [];
    const tokenHits: string[] = [];
    for (const chunk of anchoredChunks) {
      const text = chunk.text || "";
      const hits = dimension.tokens.filter((token) => text.includes(token));
      if (hits.length === 0) {
        continue;
      }
      const anchor = (chunk.source_anchors || []).find(
        (item) =>
          item.anchor_type === "detail_chunk" &&
          item.source_chunk_id === chunk.chunk_id
      );
      if (anchor) {
        anchors.push(anchor);
        for (const hit of hits) {
          if (!tokenHits.includes(hit)) {
            tokenHits.push(hit);
          }
        }
      }
    }
    if (anchors.length === 0) {
      continue;
    }

    const hasQuery = queryDimensionKeys.has(dimension.key);
    const caseLabel =
      tokenHits.length > 0
        ? `${dimension.caseLabel}：${tokenHits.slice(0, 3).join("、")}`
        : dimension.caseLabel;

    items.push({
      dimension: dimension.display,
      dimension_key: dimension.key,
      query_side_signal: hasQuery
        ? "input_signals_dimension"
        : "input_does_not_mention_dimension",
      case_side_facts: [caseLabel],
      source_anchors: anchors.slice(0, 2),
      match_type: hasQuery
        ? tokenHits.length > 0
          ? "same_dimension"
          : "similar_dimension"
        : "difference_to_review",
      confidence: hasQuery && tokenHits.length > 0 ? "medium" : "low",
      degrade_reason: null,
    });
    if (items.length >= 6) {
      break;
    }
  }

  return {
    query_session_id: detail.query_session_id || null,
    case_id: detail.case_id,
    items,
    generation_status: items.length > 0 ? "generated" : "degraded",
    degrade_reason: items.length > 0 ? null : "insufficient_source",
    query_signal_present: queryDimensionKeys.size > 0,
    timings: mockTimings,
  };
}
