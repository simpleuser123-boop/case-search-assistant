export type SearchMode = "standard" | "expand";
export type SearchResultSource = "api" | "mock";

export interface SearchRequest {
  query: string;
  mode?: "standard";
  limit?: number;
}

export interface SearchExpandRequest {
  query: string;
  mode?: "expand";
  limit?: number;
}

export interface SearchTimings {
  rewrite_duration_ms: number;
  embedding_duration_ms: number;
  retrieval_duration_ms: number;
  rerank_duration_ms: number;
  summary_duration_ms: number;
  total_duration_ms: number;
}

export interface SourceAnchor {
  case_id: string;
  source_chunk_id: string;
  chunk_type?: string | null;
  anchor_type: string;
  source_url?: string | null;
  source_ref?: string | null;
}

export interface DataCoverage {
  data_source: string;
  data_until: string;
  index_version: string;
  total_candidate_count: number | null;
  search_mode: "standard" | "expanded" | string;
  degraded_reasons: string[];
}

export type RiskType =
  | "fact_difference"
  | "key_element_missing"
  | "low_confidence_candidate"
  | "adverse_tendency_source"
  | "degraded_or_uncertain";

export interface RiskHint {
  risk_type: RiskType | string;
  source_anchors: SourceAnchor[];
  confidence_level: "high" | "medium" | "low" | string;
  confidence_reasons: string[];
  reason_code: string;
  review_note?: string | null;
}

export interface SearchSummary {
  text?: string;
  source_chunk_id?: string;
  source_case_id?: string;
  source_anchors?: SourceAnchor[];
  method?: string;
  degraded_reason?: string;
}

export interface SearchHighlight {
  text?: string;
  source_chunk_id?: string;
  source_anchors?: SourceAnchor[];
  start_offset?: number;
  end_offset?: number;
  matched_terms?: string[];
  reason?: string;
}

export interface HoldingSummaryItem {
  text?: string;
  source_anchors?: SourceAnchor[];
  confidence?: "high" | "medium" | "low" | string;
}

export interface HoldingSummary {
  summary_items: HoldingSummaryItem[];
  source_anchors: SourceAnchor[];
  confidence: "high" | "medium" | "low" | string;
  generation_status: "generated" | "degraded" | string;
  degrade_reason?: string | null;
}

export type ReadingNavigationCategory =
  | "争议焦点"
  | "裁判理由中的关键事实"
  | "法院认定的关键要素"
  | "与用户阅读相关的程序或证据节点";

export interface ReadingNavigationItem {
  label?: string;
  category?: ReadingNavigationCategory | string;
  source_anchors?: SourceAnchor[];
  confidence?: "high" | "medium" | "low" | string;
  degrade_reason?: string | null;
}

export interface ReadingNavigationSection {
  items?: ReadingNavigationItem[];
  source_anchors?: SourceAnchor[];
  generation_status?: "generated" | "degraded" | string;
  degrade_reason?: string | null;
}

export interface SearchResultItem {
  case_id: string;
  chunk_id?: string | null;
  top_chunk_id?: string | null;
  source_chunk_ids: string[];
  source_anchors?: SourceAnchor[];
  hit_chunk_ids: string[];
  retrieval_source: string[];
  vector_score?: number | null;
  fallback_score?: number | null;
  retrieval_score?: number | null;
  final_score?: number | null;
  score_breakdown: Record<string, unknown>;
  title?: string | null;
  case_no?: string | null;
  court?: string | null;
  court_level?: string | null;
  trial_level?: string | null;
  case_cause?: string | null;
  judgment_date?: string | null;
  similarity_score?: number | null;
  confidence?: "high" | "medium" | "low" | string | null;
  confidence_level?: "high" | "medium" | "low" | string | null;
  confidence_reasons?: string[];
  confidence_score_band?: string | null;
  original_rank?: number | null;
  summary?: SearchSummary | null;
  highlights: SearchHighlight[];
  source_url?: string | null;
  metadata: Record<string, unknown>;
  matched_text?: string | null;
}

export type FactMatchType =
  | "same_dimension"
  | "similar_dimension"
  | "difference_to_review";

export type FactQuerySignal =
  | "input_signals_dimension"
  | "input_does_not_mention_dimension";

export interface FactAlignmentItem {
  dimension: string;
  dimension_key: string;
  query_side_signal: FactQuerySignal | string;
  case_side_facts: string[];
  source_anchors?: SourceAnchor[];
  match_type: FactMatchType | string;
  confidence?: "high" | "medium" | "low" | string;
  degrade_reason?: string | null;
}

export interface FactAlignmentResponse {
  query_session_id?: string | null;
  case_id: string;
  items: FactAlignmentItem[];
  generation_status: "generated" | "degraded" | string;
  degrade_reason?: string | null;
  query_signal_present: boolean;
  timings?: SearchTimings;
}

export interface FactAlignmentResult {
  response: FactAlignmentResponse;
  source: SearchResultSource;
}

export interface SimilarityHighlight {
  highlight_id: string;
  case_id: string;
  source_chunk_id: string;
  anchor_type?: string;
  related_module: "holding_summary" | "issue_focus" | "key_elements" | string;
  display_status?: "available" | "degraded" | string;
  degrade_reason?: string | null;
}

export interface CaseChunk {
  chunk_id: string;
  chunk_type?: string | null;
  source_anchors?: SourceAnchor[];
  start_offset?: number | null;
  end_offset?: number | null;
  text?: string | null;
}

export interface CaseDetailResponse {
  query_session_id?: string | null;
  case_id: string;
  case_no?: string | null;
  title?: string | null;
  court?: string | null;
  court_level?: string | null;
  trial_level?: string | null;
  case_cause?: string | null;
  judgment_date?: string | null;
  region?: string | null;
  source_url?: string | null;
  source_name?: string | null;
  holding_summary?: HoldingSummary | null;
  issue_focus?: ReadingNavigationSection | null;
  key_elements?: ReadingNavigationSection | null;
  similarity_highlights?: SimilarityHighlight[];
  chunks: CaseChunk[];
  degraded: boolean;
  degraded_reasons: string[];
  timings: SearchTimings;
}

export interface SearchResponse {
  query_session_id: string;
  candidates: SearchResultItem[];
  results: SearchResultItem[];
  low_confidence_candidates: SearchResultItem[];
  risk_hints: RiskHint[];
  coverage: DataCoverage;
  degraded: boolean;
  degraded_reasons: string[];
  retrieval_duration_ms: number;
  timings: SearchTimings;
}

export interface SearchApiErrorDetail {
  code?: string;
  message?: string;
  query_session_id?: string | null;
}

export interface SearchApiErrorResponse {
  error?: SearchApiErrorDetail;
}

export interface SearchCasesResult {
  response: SearchResponse;
  source: SearchResultSource;
}

export interface CaseDetailResult {
  response: CaseDetailResponse;
  source: SearchResultSource;
}
