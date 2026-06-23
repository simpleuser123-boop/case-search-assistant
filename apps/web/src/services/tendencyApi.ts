// M5-8 法院/法官倾向分析（F19）API 客户端（前端）。
//
// 边界 / 隐私红线：
//   - 只读聚合统计：仅 GET 拉取后端聚合（法院层级/审级/案件领域/案由分布 + 占比 +
//     可追溯 case_id 引用 + 覆盖/样本/免责说明）。绝不接收/渲染个案正文或当事人。
//   - 双闸：后端在 ENABLE_TENDENCY_ANALYSIS=false 或 M5-7 数据门禁未达标时返回 403
//     TENDENCY_ANALYSIS_UNAVAILABLE；前端据此不展示，回到 M5-7 末态。
//   - 不输出个案预测 / 胜负概率 / 确定性法律结论：响应只含聚合分布，前端原样展示。
//   - 不参与主排序 / 召回 / source selection。

export const TENDENCY_API_BASE = "/api/tendency";

export type TendencyApiResult<T> =
  | { ok: true; data: T }
  | {
      ok: false;
      reason: "unavailable" | "network_error" | "http_error";
      status?: number;
      reasonCode?: string;
    };

export type TendencyBucket = {
  label: string;
  sample_size: number;
  share: number;
  sample_sufficient: boolean;
  case_id_refs: string[];
  case_id_total: number;
};

export type TendencyAggregation = {
  dimension: string;
  name: string;
  sample_size: number;
  coverage_range: string;
  data_source: string;
  confidence_note: string;
  insufficient_dimension: boolean;
  buckets: TendencyBucket[];
};

export type TendencyAnalysis = {
  version: string;
  enabled: boolean;
  gate_passed: boolean;
  data_source: string;
  coverage_range: string;
  total_sample_size: number;
  min_sample_threshold: number;
  disclaimer: string;
  aggregations: TendencyAggregation[];
};

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === "string");
}

function isTendencyBucket(value: unknown): value is TendencyBucket {
  if (!value || typeof value !== "object") return false;
  const bucket = value as Partial<TendencyBucket>;
  return (
    typeof bucket.label === "string" &&
    typeof bucket.sample_size === "number" &&
    typeof bucket.share === "number" &&
    typeof bucket.sample_sufficient === "boolean" &&
    isStringArray(bucket.case_id_refs) &&
    typeof bucket.case_id_total === "number"
  );
}

function isTendencyAggregation(value: unknown): value is TendencyAggregation {
  if (!value || typeof value !== "object") return false;
  const agg = value as Partial<TendencyAggregation>;
  return (
    typeof agg.dimension === "string" &&
    typeof agg.name === "string" &&
    typeof agg.sample_size === "number" &&
    typeof agg.coverage_range === "string" &&
    typeof agg.data_source === "string" &&
    typeof agg.confidence_note === "string" &&
    typeof agg.insufficient_dimension === "boolean" &&
    Array.isArray(agg.buckets) &&
    agg.buckets.every(isTendencyBucket)
  );
}

function isTendencyAnalysis(value: unknown): value is TendencyAnalysis {
  if (!value || typeof value !== "object") return false;
  const data = value as Partial<TendencyAnalysis>;
  return (
    typeof data.version === "string" &&
    typeof data.enabled === "boolean" &&
    typeof data.gate_passed === "boolean" &&
    typeof data.data_source === "string" &&
    typeof data.coverage_range === "string" &&
    typeof data.total_sample_size === "number" &&
    typeof data.min_sample_threshold === "number" &&
    typeof data.disclaimer === "string" &&
    Array.isArray(data.aggregations) &&
    data.aggregations.every(isTendencyAggregation)
  );
}

function classify(status: number): "unavailable" | "http_error" {
  if (status === 403) return "unavailable";
  return "http_error";
}

export async function fetchTendencyAnalysis(): Promise<TendencyApiResult<TendencyAnalysis>> {
  if (typeof fetch === "undefined") {
    return { ok: false, reason: "network_error" };
  }
  let resp: Response;
  try {
    resp = await fetch(`${TENDENCY_API_BASE}/analysis`, {
      method: "GET",
      headers: { Accept: "application/json" },
    });
  } catch {
    return { ok: false, reason: "network_error" };
  }
  if (!resp.ok) {
    let reasonCode: string | undefined;
    try {
      const errBody = (await resp.json()) as { error?: { code?: string } };
      reasonCode = errBody?.error?.code;
    } catch {
      reasonCode = undefined;
    }
    return { ok: false, reason: classify(resp.status), status: resp.status, reasonCode };
  }
  let data: unknown;
  try {
    data = await resp.json();
  } catch {
    return { ok: false, reason: "http_error", status: resp.status };
  }
  if (!isTendencyAnalysis(data)) {
    return { ok: false, reason: "http_error", status: resp.status };
  }
  return { ok: true, data };
}
