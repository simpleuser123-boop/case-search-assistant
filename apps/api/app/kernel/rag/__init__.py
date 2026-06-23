"""共享内核 · RAG 核心组公开面（E-2a 逻辑边界，纯 re-export）。

内核成员（依据文档 17 §2.1）：retrieval / rerank / query_processing / summary。
本模块只把上述四包「现有可调用入口」收敛为稳定公开符号，**纯 re-export**：
不复制实现、不改签名、不改运行时语义、不新增逻辑。E-2a 阶段零文件移动，
真实实现仍在原 app.kernel.rag.retrieval / app.kernel.rag.rerank / app.kernel.rag.query_processing / app.kernel.rag.summary。

消费方（检索链路、未来产品包）应经 app.kernel 公开面消费这些符号，
不得再 `from app.kernel.rag.retrieval.xxx import 内部私有符号` 式深引内核实现细节。

E3-1 新增：内部检索服务契约（SearchProfile -> CandidateRef 的纯模型 + 纯函数），
经本公开面导出稳定符号，供 E3-2 检索执行服务适配层与未来产品包消费；本步不接线检索链路。
"""
from __future__ import annotations

# --- retrieval 包公开面（含 service / models / merge 等既有入口）---
from app.kernel.rag.retrieval import (
    BM25_FALLBACK_SOURCE,
    BM25_FALLBACK_FAILED,
    BM25_FALLBACK_USED,
    BM25_RELAXED_RECALL_SOURCE,
    BM25FallbackRetriever,
    CaseCandidate,
    CHROMA_EMPTY,
    CHROMA_QUERY_FAILED,
    CHROMA_QUERY_TIMEOUT,
    CHROMA_UNAVAILABLE,
    ChromaCollectionAdapter,
    ChromaProbeResult,
    DEFAULT_SOFT_FILTER_WEIGHTS,
    EMBEDDING_MODEL_MISMATCH,
    EMBEDDING_TIMEOUT,
    EMBEDDING_UNAVAILABLE,
    EmbeddingResult,
    MIN_CANDIDATES_BEFORE_RELAXED_RECALL,
    merge_case_candidates,
    OllamaEmbeddingClient,
    ORIGINAL_VECTOR_SOURCE,
    ORIGINAL_VECTOR_TOP_K,
    RELAXED_RECALL_TOP_K,
    RetrievalConfigMismatchError,
    RetrievalDependencyError,
    RetrievedChunk,
    SoftFilterWeights,
    VARIANT_VECTOR_SOURCE,
    VARIANT_VECTOR_TOP_K,
    VectorCandidate,
    VectorRetrievalResult,
    VectorRetrievalService,
)

# retrieval 置信度分层 / 风险提示：现有检索链路真实依赖的入口，
# 经公开面收敛后消费方不再深引 app.kernel.rag.retrieval.confidence / .risk_hints。
from app.kernel.rag.retrieval.confidence import (
    ConfidenceProfile,
    ConfidenceSplit,
    LayeredRankedCandidate,
    build_confidence_profile,
    split_low_confidence_candidates,
)
from app.kernel.rag.retrieval.risk_hints import build_risk_hints

# --- rerank 包公开面 ---
from app.kernel.rag.rerank import (
    DEFAULT_RERANK_WEIGHTS,
    FactSimilarityReranker,
    RankedCaseCandidate,
    RerankWeights,
)

# --- query_processing 包公开面 ---
from app.kernel.rag.query_processing import (
    DeepSeekClient,
    QueryPlan,
    QueryProcessingService,
    QueryRewriteLLMOutput,
    QueryValidationError,
    clean_query,
    input_hash_for_query,
)

# --- E3-1 内部检索服务契约（SearchProfile -> CandidateRef，纯模型 + 纯函数，不接线）---
from app.kernel.rag.internal_search_contracts import (
    CandidateRef,
    InternalSearchMode,
    InternalSearchRequest,
    InternalSearchResult,
    SearchProfile,
    SearchProfileInput,
    SourceAnchorRef,
    sanitize_candidate_ref,
    sanitize_search_profile,
    search_result_item_to_candidate_ref,
)

# --- E3-2 检索执行服务适配层（内部服务，经公开面消费 RAG 四组，输出 CandidateRef[]）---
from app.kernel.rag.internal_search_service import (
    CANDIDATE_REF_DROPPED_NO_ANCHOR,
    InternalSearchExecutionResult,
    InternalSearchService,
)

# --- E5-3 内核法条检索服务（查询/类案 -> StatuteRef[]，法条 -> CandidateRef[] 互跳）---
# 经子包公开面消费 E3 契约 / 查询规范化 / StatuteRef 护栏；不从本聚合 __init__ 回引。
from app.kernel.rag.statute_search_service import (
    CaseLinkHit,
    JsonlStatuteCorpus,
    STATUTE_CASE_REF_DROPPED_NO_ANCHOR,
    STATUTE_REF_DROPPED_NO_ANCHOR,
    StatuteCaseRefResult,
    StatuteCorpusPort,
    StatuteHit,
    StatuteSearchResult,
    StatuteSearchService,
)

# --- summary 包公开面 ---
from app.kernel.rag.summary import (
    FACT_ALIGNMENT_FAILED,
    FACT_ALIGNMENT_INSUFFICIENT_SOURCE,
    FACT_ALIGNMENT_MISSING_QUERY_SIGNAL,
    FACT_ALIGNMENT_MISSING_SOURCE_ANCHOR,
    FACT_ALIGNMENT_REASON_CODES,
    FACT_ALIGNMENT_TIMEOUT,
    FactAlignmentService,
    HIGHLIGHT_REASON_CODES,
    HighlightItem,
    HOLDING_INSUFFICIENT_SOURCE,
    HOLDING_MISSING_SOURCE_ANCHOR,
    HOLDING_MODEL_FAILED,
    HOLDING_SOURCE_MISMATCH,
    MATCH_DIFFERENCE,
    MATCH_SAME,
    MATCH_SIMILAR,
    MATCH_TYPES,
    MODULE_HOLDING_SUMMARY,
    MODULE_ISSUE_FOCUS,
    MODULE_KEY_ELEMENTS,
    QUERY_SIGNAL_ABSENT,
    QUERY_SIGNAL_PRESENT,
    READING_ALLOWED_CATEGORIES,
    REASON_HIGHLIGHT_TARGET_MISSING,
    REASON_MISSING_SOURCE_ANCHOR,
    REASON_NAVIGATION_FAILED,
    REASON_SOURCE_CHUNK_UNAVAILABLE,
    ResultPresentation,
    STATUS_AVAILABLE,
    STATUS_DEGRADED,
    SUMMARY_DISABLED,
    SUMMARY_LLM_INVALID_JSON,
    SUMMARY_LLM_SCHEMA_INVALID,
    SUMMARY_LLM_TIMEOUT,
    SUMMARY_LLM_UNAVAILABLE,
    SUMMARY_SOURCE_MISSING,
    SourceChunk,
    SummaryItem,
    SummaryService,
    build_similarity_highlights,
    summarize_highlights,
)

__all__ = [
    # retrieval
    "BM25_FALLBACK_SOURCE", "BM25_FALLBACK_FAILED", "BM25_FALLBACK_USED",
    "BM25_RELAXED_RECALL_SOURCE", "BM25FallbackRetriever", "CaseCandidate",
    "CHROMA_EMPTY", "CHROMA_QUERY_FAILED", "CHROMA_QUERY_TIMEOUT", "CHROMA_UNAVAILABLE",
    "ChromaCollectionAdapter", "ChromaProbeResult", "DEFAULT_SOFT_FILTER_WEIGHTS",
    "EMBEDDING_MODEL_MISMATCH", "EMBEDDING_TIMEOUT", "EMBEDDING_UNAVAILABLE",
    "EmbeddingResult", "MIN_CANDIDATES_BEFORE_RELAXED_RECALL", "merge_case_candidates",
    "OllamaEmbeddingClient", "ORIGINAL_VECTOR_SOURCE", "ORIGINAL_VECTOR_TOP_K",
    "RELAXED_RECALL_TOP_K", "RetrievalConfigMismatchError", "RetrievalDependencyError",
    "RetrievedChunk", "SoftFilterWeights", "VARIANT_VECTOR_SOURCE", "VARIANT_VECTOR_TOP_K",
    "VectorCandidate", "VectorRetrievalResult", "VectorRetrievalService",
    "ConfidenceProfile", "ConfidenceSplit", "LayeredRankedCandidate",
    "build_confidence_profile", "split_low_confidence_candidates", "build_risk_hints",
    # rerank
    "DEFAULT_RERANK_WEIGHTS", "FactSimilarityReranker", "RankedCaseCandidate", "RerankWeights",
    # query_processing
    "DeepSeekClient", "QueryPlan", "QueryProcessingService", "QueryRewriteLLMOutput",
    "QueryValidationError", "clean_query", "input_hash_for_query",
    # summary
    "FACT_ALIGNMENT_FAILED", "FACT_ALIGNMENT_INSUFFICIENT_SOURCE",
    "FACT_ALIGNMENT_MISSING_QUERY_SIGNAL", "FACT_ALIGNMENT_MISSING_SOURCE_ANCHOR",
    "FACT_ALIGNMENT_REASON_CODES", "FACT_ALIGNMENT_TIMEOUT", "FactAlignmentService",
    "HIGHLIGHT_REASON_CODES", "HighlightItem", "HOLDING_INSUFFICIENT_SOURCE",
    "HOLDING_MISSING_SOURCE_ANCHOR", "HOLDING_MODEL_FAILED", "HOLDING_SOURCE_MISMATCH",
    "MATCH_DIFFERENCE", "MATCH_SAME", "MATCH_SIMILAR", "MATCH_TYPES",
    "MODULE_HOLDING_SUMMARY", "MODULE_ISSUE_FOCUS", "MODULE_KEY_ELEMENTS",
    "QUERY_SIGNAL_ABSENT", "QUERY_SIGNAL_PRESENT", "READING_ALLOWED_CATEGORIES",
    "REASON_HIGHLIGHT_TARGET_MISSING", "REASON_MISSING_SOURCE_ANCHOR",
    "REASON_NAVIGATION_FAILED", "REASON_SOURCE_CHUNK_UNAVAILABLE", "ResultPresentation",
    "STATUS_AVAILABLE", "STATUS_DEGRADED", "SUMMARY_DISABLED", "SUMMARY_LLM_INVALID_JSON",
    "SUMMARY_LLM_SCHEMA_INVALID", "SUMMARY_LLM_TIMEOUT", "SUMMARY_LLM_UNAVAILABLE",
    "SUMMARY_SOURCE_MISSING", "SourceChunk", "SummaryItem", "SummaryService",
    "build_similarity_highlights", "summarize_highlights",
    # E3-1 内部检索服务契约
    "SearchProfile", "SearchProfileInput", "CandidateRef", "SourceAnchorRef",
    "InternalSearchMode", "InternalSearchRequest", "InternalSearchResult",
    "sanitize_search_profile", "sanitize_candidate_ref",
    "search_result_item_to_candidate_ref",
    # E3-2 检索执行服务适配层
    "InternalSearchService", "InternalSearchExecutionResult",
    "CANDIDATE_REF_DROPPED_NO_ANCHOR",
    # E5-3 内核法条检索服务
    "StatuteSearchService", "StatuteSearchResult", "StatuteCaseRefResult",
    "StatuteCorpusPort", "JsonlStatuteCorpus", "StatuteHit", "CaseLinkHit",
    "STATUTE_REF_DROPPED_NO_ANCHOR", "STATUTE_CASE_REF_DROPPED_NO_ANCHOR",
]
