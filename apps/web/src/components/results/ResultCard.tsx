import { HighlightText } from "./HighlightText";
import { SimilarityMeter } from "./SimilarityMeter";
import { CopyCitationButton } from "./CopyCitationButton";
import { FavoriteButton } from "./FavoriteButton";
import { AddToListButton, type ListSelectionState } from "./AddToListButton";
import { buildCitationFromResult } from "../../lib/citationCopy";
import type { FeedbackSelection } from "../../services/feedbackApi";
import type { SearchHighlight, SearchResultItem, SourceAnchor } from "../../types/search";

export type ResultFeedbackState = {
  value: FeedbackSelection | null;
  isPending?: boolean;
  error?: boolean;
};

export type CompareSelectionState = {
  checked: boolean;
  disabled: boolean;
  onToggle: (result: SearchResultItem) => void;
};

// M4-3: 收藏切换状态（由父组件管理；flag 关闭时父组件不传，按钮不渲染）。
export type FavoriteSelectionState = {
  favorited: boolean;
  onToggle: (result: SearchResultItem) => void;
};

// M4-4: 清单选择状态（由父组件管理；flag 关闭时父组件不传，按钮不渲染）。
export type ResultListSelectionState = {
  selection: ListSelectionState;
};

type ResultCardProps = {
  result: SearchResultItem;
  index: number;
  onSelect?: (result: SearchResultItem, triggerElement: HTMLElement) => void;
  feedback?: ResultFeedbackState;
  onFeedback?: (result: SearchResultItem, value: FeedbackSelection) => void;
  variant?: "primary" | "lowConfidence";
  compareSelection?: CompareSelectionState;
  favoriteSelection?: FavoriteSelectionState;
  listSelection?: ListSelectionState;
};

export function ResultCard({
  result,
  index,
  onSelect,
  feedback,
  onFeedback,
  variant = "primary",
  compareSelection,
  favoriteSelection,
  listSelection,
}: ResultCardProps) {
  const score =
    result.final_score ?? result.similarity_score ?? result.retrieval_score ?? null;
  const summaryText = getSummaryText(result);
  const visibleHighlights = result.highlights.filter(hasHighlightAnchor);
  const title = result.title?.trim() || "未命名案例";
  const confidenceLevel = result.confidence_level ?? result.confidence;
  const isLowConfidence = variant === "lowConfidence" || confidenceLevel === "low";
  const metaItems = [
    result.case_no || "案号暂缺",
    result.court || "法院暂缺",
    result.trial_level || result.court_level || "审级暂缺",
    result.case_cause || "案由暂缺",
    result.judgment_date || "日期暂缺",
  ];
  const sourceAnchors = (result.source_anchors || []).filter(isSourceAnchor).slice(0, 4);

  return (
    <article
      tabIndex={-1}
      className={[
        "cursor-pointer rounded-[8px] border bg-[var(--color-surface)] p-4 transition hover:shadow-sm focus:outline-none focus:ring-2 focus:ring-[var(--color-brand-soft)] sm:p-5",
        isLowConfidence
          ? "border-[var(--color-border)] hover:border-[var(--color-warning)]"
          : "border-[var(--color-border)] hover:border-[var(--color-border-strong)]",
      ].join(" ")}
      onClick={(event) => onSelect?.(result, event.currentTarget)}
    >
      <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div className="min-w-0 flex-1">
          <div className="flex items-start gap-2">
            <span
              className={[
                "mt-0.5 shrink-0 rounded-[4px] px-2 py-1 text-xs font-medium",
                isLowConfidence
                  ? "bg-[var(--color-warning-soft)] text-[var(--color-warning)]"
                  : "bg-[var(--color-brand-soft)] text-[var(--color-brand)]",
              ].join(" ")}
            >
              {isLowConfidence ? `候选 #${index + 1}` : `#${index + 1}`}
            </span>
            <div className="min-w-0">
              {isLowConfidence ? (
                <span className="mb-2 inline-flex rounded-[4px] border border-[var(--color-warning)] bg-[var(--color-warning-soft)] px-2 py-1 text-[11px] font-medium text-[var(--color-warning)]">
                  部分相关，仅供复核
                </span>
              ) : null}
              <h2 className="break-words text-base font-semibold leading-6 text-[var(--color-text)]">
                {title}
              </h2>
              <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-xs leading-5 text-[var(--color-text-muted)]">
                {metaItems.map((item) => (
                  <span key={item}>{item}</span>
                ))}
              </div>
            </div>
          </div>
        </div>

        <SimilarityMeter score={score} />
      </div>

      <div className="mt-4 space-y-3">
        <div>
          <p className="text-xs font-medium text-[var(--color-text-muted)]">
            事实摘要
          </p>
          <p className="mt-1 text-sm leading-6 text-[var(--color-text)]">
            {summaryText}
          </p>
          {result.summary?.degraded_reason ? (
            <p className="mt-1 text-xs text-[var(--color-warning)]">
              摘要生成降级：{formatReason(result.summary.degraded_reason)}
            </p>
          ) : null}
        </div>

        {visibleHighlights.length > 0 ? (
          <div>
            <p className="text-xs font-medium text-[var(--color-text-muted)]">
              高亮事实片段
            </p>
            <ul className="mt-2 grid gap-2 text-sm leading-6">
              {visibleHighlights.slice(0, 3).map((highlight, highlightIndex) => (
                <HighlightText
                  key={`${highlight.source_chunk_id || "highlight"}-${highlightIndex}`}
                  highlight={highlight}
                />
              ))}
            </ul>
          </div>
        ) : null}

        <div className="flex flex-col gap-3 border-t border-[var(--color-border)] pt-3 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex min-w-0 flex-wrap gap-2">
            {sourceAnchors.length > 0 ? (
              sourceAnchors.map((anchor) => (
                <span
                  key={`${anchor.case_id}-${anchor.source_chunk_id}-${anchor.anchor_type}`}
                  className="max-w-full truncate rounded-[4px] bg-[var(--color-surface-muted)] px-2 py-1 font-mono text-[11px] text-[var(--color-text-muted)]"
                  title={formatAnchorTitle(anchor)}
                >
                  来源 {anchor.source_chunk_id}
                </span>
              ))
            ) : (
              <span className="text-xs text-[var(--color-text-subtle)]">
                来源片段暂缺
              </span>
            )}
            {result.retrieval_source.slice(0, 3).map((source) => (
              <span
                key={source}
                className="rounded-[4px] border border-[var(--color-border)] px-2 py-1 text-[11px] text-[var(--color-text-muted)]"
              >
                {formatRetrievalSource(source)}
              </span>
            ))}
            {isLowConfidence && (result.confidence_reasons || []).length > 0 ? (
              <span className="rounded-[4px] border border-[var(--color-warning)] px-2 py-1 text-[11px] text-[var(--color-warning)]">
                {formatConfidenceReason(result.confidence_reasons?.[0] || "")}
              </span>
            ) : null}
          </div>

          <div className="flex shrink-0 items-center gap-2">
            <CopyCitationButton
              record={buildCitationFromResult(result)}
              kind="case_number"
              surface="result_card"
              label="复制案号"
              ariaLabel={`复制案号：${title}`}
            />
            {favoriteSelection ? (
              <FavoriteButton
                favorited={favoriteSelection.favorited}
                caseTitle={title}
                stopPropagation
                onToggle={() => favoriteSelection.onToggle(result)}
              />
            ) : null}
            {listSelection ? (
              <AddToListButton selection={listSelection} caseTitle={title} stopPropagation />
            ) : null}
            {compareSelection ? (
              <label
                className={[
                  "inline-flex h-9 cursor-pointer select-none items-center gap-1.5 rounded-[8px] border px-3 text-sm font-medium transition focus-within:ring-2 focus-within:ring-[var(--color-brand-soft)]",
                  compareSelection.checked
                    ? "border-[var(--color-brand)] bg-[var(--color-brand-soft)] text-[var(--color-brand)]"
                    : compareSelection.disabled
                      ? "cursor-not-allowed border-[var(--color-border)] text-[var(--color-text-subtle)]"
                      : "border-[var(--color-border-strong)] text-[var(--color-text-muted)] hover:bg-[var(--color-surface-muted)]",
                ].join(" ")}
                title={
                  compareSelection.disabled && !compareSelection.checked
                    ? "已达到本次对比的案例数量上限"
                    : "选择此案例加入本次阅读对比"
                }
                onClick={(event) => event.stopPropagation()}
              >
                <input
                  type="checkbox"
                  className="h-3.5 w-3.5 accent-[var(--color-brand)]"
                  checked={compareSelection.checked}
                  disabled={compareSelection.disabled && !compareSelection.checked}
                  aria-label={`将案例加入对比：${title}`}
                  onChange={(event) => {
                    event.stopPropagation();
                    compareSelection.onToggle(result);
                  }}
                />
                对比
              </label>
            ) : null}
            <button
              type="button"
              aria-label={`查看案例详情：${title}`}
              className="inline-flex h-9 shrink-0 items-center justify-center rounded-[8px] border border-[var(--color-border-strong)] bg-[var(--color-surface)] px-3 text-sm font-medium text-[var(--color-brand)] transition hover:bg-[var(--color-brand-soft)] focus:outline-none focus:ring-2 focus:ring-[var(--color-brand-soft)]"
              onClick={(event) => {
                event.stopPropagation();
                onSelect?.(result, event.currentTarget);
              }}
            >
              查看详情
            </button>
          </div>
        </div>

        {onFeedback ? (
          <FeedbackControls
            title={title}
            feedback={feedback}
            onFeedback={(value) => onFeedback(result, value)}
          />
        ) : null}
      </div>
    </article>
  );
}

function FeedbackControls({
  title,
  feedback,
  onFeedback,
}: {
  title: string;
  feedback?: ResultFeedbackState;
  onFeedback: (value: FeedbackSelection) => void;
}) {
  const statusText = getFeedbackStatusText(feedback);

  return (
    <div className="flex flex-col gap-2 border-t border-[var(--color-border)] pt-3 sm:flex-row sm:items-center sm:justify-between">
      <div className="flex gap-2" aria-label={`结果反馈：${title}`}>
        <FeedbackButton
          value="relevant"
          title={title}
          selected={feedback?.value === "relevant"}
          disabled={feedback?.isPending}
          onFeedback={onFeedback}
        />
        <FeedbackButton
          value="not_relevant"
          title={title}
          selected={feedback?.value === "not_relevant"}
          disabled={feedback?.isPending}
          onFeedback={onFeedback}
        />
      </div>
      {statusText ? (
        <span className="text-xs text-[var(--color-text-muted)]">{statusText}</span>
      ) : null}
    </div>
  );
}

function FeedbackButton({
  value,
  title,
  selected,
  disabled,
  onFeedback,
}: {
  value: FeedbackSelection;
  title: string;
  selected?: boolean;
  disabled?: boolean;
  onFeedback: (value: FeedbackSelection) => void;
}) {
  const label = value === "relevant" ? "相关" : "不相关";
  const action = selected
    ? value === "relevant"
      ? "撤销相关标记"
      : "撤销不相关标记"
    : value === "relevant"
      ? "标记为相关"
      : "标记为不相关";

  return (
    <button
      type="button"
      aria-label={`${action}：${title}`}
      aria-pressed={selected}
      disabled={disabled}
      className={[
        "inline-flex h-8 items-center justify-center rounded-[8px] border px-3 text-xs font-medium transition focus:outline-none focus:ring-2 focus:ring-[var(--color-brand-soft)] disabled:cursor-not-allowed disabled:opacity-70",
        selected
          ? "border-[var(--color-brand)] bg-[var(--color-brand-soft)] text-[var(--color-brand)]"
          : "border-[var(--color-border-strong)] bg-[var(--color-surface)] text-[var(--color-text-muted)] hover:bg-[var(--color-surface-muted)]",
      ].join(" ")}
      onClick={(event) => {
        event.stopPropagation();
        onFeedback(value);
      }}
    >
      {label}
    </button>
  );
}

function getFeedbackStatusText(feedback?: ResultFeedbackState) {
  if (feedback?.isPending) {
    return "记录中";
  }

  if (feedback?.error) {
    return "反馈未记录";
  }

  if (feedback?.value === "relevant") {
    return "已标记相关";
  }

  if (feedback?.value === "not_relevant") {
    return "已标记不相关";
  }

  return "";
}

function getSummaryText(result: SearchResultItem) {
  const summaryText = result.summary?.text?.trim();

  if (summaryText && hasSummaryAnchor(result)) {
    return summaryText;
  }

  if (result.matched_text?.trim() && hasResultAnchor(result)) {
    return `摘要暂不可用，展示来源片段：${result.matched_text.trim()}`;
  }

  return "暂无可核验摘要来源，未展示 AI 摘要。";
}

function hasResultAnchor(result: SearchResultItem) {
  return (result.source_anchors || []).some(isSourceAnchor);
}

function hasSummaryAnchor(result: SearchResultItem) {
  const anchors = result.summary?.source_anchors || [];
  return anchors.some(
    (anchor) =>
      isSourceAnchor(anchor) &&
      anchor.anchor_type === "summary" &&
      anchor.case_id === result.case_id &&
      anchor.source_chunk_id === result.summary?.source_chunk_id
  );
}

function hasHighlightAnchor(highlight: SearchHighlight) {
  return Boolean(
    highlight.text?.trim() &&
      (highlight.source_anchors || []).some(
        (anchor) =>
          isSourceAnchor(anchor) &&
          anchor.anchor_type === "highlight" &&
          anchor.source_chunk_id === highlight.source_chunk_id
      )
  );
}

function isSourceAnchor(anchor: SourceAnchor | undefined | null): anchor is SourceAnchor {
  return Boolean(anchor?.case_id?.trim() && anchor.source_chunk_id?.trim());
}

function formatAnchorTitle(anchor: SourceAnchor) {
  const source = anchor.source_url || anchor.source_ref || "local_case_store";
  return `case_id: ${anchor.case_id}; source_chunk_id: ${anchor.source_chunk_id}; source: ${source}`;
}

function formatRetrievalSource(source: string) {
  const labels: Record<string, string> = {
    original_vector: "原始向量召回",
    variant_vector: "改写向量召回",
    chroma_vector: "向量召回",
    chroma_vector_variant: "改写召回",
    bm25_fallback: "基础检索",
    bm25_fallback_relaxed_recall: "低置信候选",
    frontend_mock_fixture: "前端测试数据",
  };

  return labels[source] || source;
}

function formatReason(reason: string) {
  const labels: Record<string, string> = {
    SUMMARY_DISABLED: "摘要生成已关闭，显示可复核来源片段",
    SUMMARY_LLM_UNAVAILABLE: "摘要增强不可用，显示可复核片段",
    SUMMARY_LLM_TIMEOUT: "摘要增强超时，显示可复核片段",
    SUMMARY_SOURCE_MISSING: "来源片段不足",
  };

  return labels[reason] || reason;
}

function formatConfidenceReason(reason: string) {
  const labels: Record<string, string> = {
    LOW_SCORE_BAND: "分数区间较低",
    RELAXED_RECALL_SOURCE: "放宽召回来源",
    LOW_LEGAL_ELEMENT_HIT_COUNT: "法律要素命中较少",
    DEGRADED_SEARCH_PATH: "降级路径候选",
    MAIN_RESULT_COUNT_BELOW_TARGET: "主结果较少时展示",
  };

  return labels[reason] || "候选需复核";
}
