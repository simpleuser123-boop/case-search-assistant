export function formatDegradedReason(reason: string) {
  const labels: Record<string, string> = {
    QUERY_REWRITE_DISABLED: "案情改写未启用，使用原始输入检索。",
    LLM_TIMEOUT: "案情改写超时，使用原始输入检索。",
    LLM_INVALID_JSON: "案情改写返回异常，使用原始输入检索。",
    LLM_SCHEMA_INVALID: "案情改写结构异常，使用原始输入检索。",
    CHROMA_QUERY_FAILED: "向量召回异常，已回退到基础检索。",
    CHROMA_QUERY_TIMEOUT: "向量召回超时，已回退到基础检索。",
    CHROMA_UNAVAILABLE: "向量库不可用，已回退到基础检索。",
    CHROMA_EMPTY: "向量库暂无可查询内容，已回退到基础检索。",
    EMBEDDING_TIMEOUT: "向量生成超时，已回退到基础检索。",
    EMBEDDING_UNAVAILABLE: "向量生成不可用，已回退到基础检索。",
    EMBEDDING_MODEL_MISMATCH: "向量模型不匹配，已回退到基础检索。",
    BM25_FALLBACK_USED: "已使用基础关键词检索策略。",
    BM25_FALLBACK_FAILED: "基础关键词检索不可用，当前返回明确空状态。",
    SUMMARY_DISABLED: "摘要生成已关闭，展示可复核来源片段。",
    SUMMARY_LLM_UNAVAILABLE: "摘要增强不可用，展示可复核片段。",
    SUMMARY_LLM_TIMEOUT: "摘要增强超时，展示可复核片段。",
    SUMMARY_SOURCE_MISSING: "摘要来源片段不足。",
  };

  return labels[reason] || reason;
}
