import { formatDegradedReason } from "../../lib/searchDisplay";
import type {
  SearchResponse,
  SearchResultItem,
  SearchResultSource,
} from "../../types/search";
import type { FeedbackSelection } from "../../services/feedbackApi";
import { ResultCard, type FavoriteSelectionState, type ResultFeedbackState } from "./ResultCard";
import type { ListSelectionState } from "./AddToListButton";

type LowConfidencePanelProps = {
  primaryResults: SearchResultItem[];
  response?: SearchResponse | null;
  source?: SearchResultSource;
  isLoading: boolean;
  error?: Error | null;
  showEntry: boolean;
  onExpand: () => void;
  onSelectResult: (result: SearchResultItem, triggerElement: HTMLElement) => void;
  getFeedbackState?: (result: SearchResultItem) => ResultFeedbackState | undefined;
  onFeedback?: (result: SearchResultItem, value: FeedbackSelection) => void;
  getFavoriteSelection?: (result: SearchResultItem) => FavoriteSelectionState | undefined;
  getListSelection?: (result: SearchResultItem) => ListSelectionState | undefined;
};

export function LowConfidencePanel({
  primaryResults,
  response,
  source,
  isLoading,
  error,
  showEntry,
  onExpand,
  onSelectResult,
  getFeedbackState,
  onFeedback,
  getFavoriteSelection,
  getListSelection,
}: LowConfidencePanelProps) {
  const candidates = response ? getLowConfidenceCandidates(response, primaryResults) : [];
  const shouldRender = showEntry || isLoading || Boolean(error) || Boolean(response);
  const showCandidateAction =
    showEntry || isLoading || Boolean(error) || response?.coverage.search_mode === "expanded";

  if (!shouldRender) {
    return null;
  }

  return (
    <section
      aria-labelledby="low-confidence-heading"
      className="rounded-[8px] border border-[var(--color-warning)] bg-[var(--color-warning-soft)] px-4 py-4 sm:px-5"
    >
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <p className="text-xs font-medium text-[var(--color-warning)]">
            补充候选
          </p>
          <h2
            id="low-confidence-heading"
            className="mt-1 text-base font-semibold text-[var(--color-text)]"
          >
            部分相关，仅供复核
          </h2>
          <p className="mt-1 text-sm leading-6 text-[var(--color-text-muted)]">
            主结果较少时，可主动扩大复核范围，查看补充候选；补充候选仅供复核，不替代主结果排序。
          </p>
        </div>

        {showCandidateAction ? (
          <button
            type="button"
            disabled={isLoading}
            className="inline-flex h-10 shrink-0 items-center justify-center rounded-[8px] border border-[var(--color-warning)] bg-[var(--color-surface)] px-3 text-sm font-medium text-[var(--color-warning)] transition hover:bg-white focus:outline-none focus:ring-2 focus:ring-[var(--color-brand-soft)] disabled:cursor-not-allowed disabled:opacity-70"
            onClick={onExpand}
          >
            {isLoading
              ? "补充候选加载中..."
              : response?.coverage.search_mode === "expanded"
                ? "继续扩大复核范围"
                : "扩大复核范围"}
          </button>
        ) : null}
      </div>

      {source === "mock" && response ? (
        <div className="mt-3 rounded-[8px] border border-[var(--color-warning)] bg-white px-3 py-2 text-xs leading-5 text-[var(--color-text)]">
          当前低置信度候选使用前端测试数据，所有案例均为非真实样例。
        </div>
      ) : null}

      {isLoading ? (
        <LowConfidenceSkeleton />
      ) : error ? (
        <LowConfidenceError error={error} onRetry={onExpand} />
      ) : response ? (
        <LowConfidenceResults
          response={response}
          candidates={candidates}
          onSelectResult={onSelectResult}
          getFeedbackState={getFeedbackState}
          onFeedback={onFeedback}
          getFavoriteSelection={getFavoriteSelection}
          getListSelection={getListSelection}
        />
      ) : null}
    </section>
  );
}

function LowConfidenceSkeleton() {
  return (
    <div aria-label="补充候选加载中" className="mt-4 grid gap-3">
      {[0, 1].map((item) => (
        <div
          key={item}
          className="rounded-[8px] border border-[var(--color-border)] bg-[var(--color-surface)] p-4"
        >
          <div className="h-4 w-32 animate-pulse rounded bg-[var(--color-surface-muted)]" />
          <div className="mt-3 h-5 w-4/5 animate-pulse rounded bg-[var(--color-surface-muted)]" />
          <div className="mt-4 space-y-2">
            <div className="h-4 w-full animate-pulse rounded bg-[var(--color-surface-muted)]" />
            <div className="h-4 w-2/3 animate-pulse rounded bg-[var(--color-surface-muted)]" />
          </div>
        </div>
      ))}
    </div>
  );
}

function LowConfidenceError({
  error,
  onRetry,
}: {
  error: Error;
  onRetry: () => void;
}) {
  return (
    <div
      role="alert"
      className="mt-4 rounded-[8px] border border-[var(--color-danger)] bg-[var(--color-danger-soft)] px-3 py-3 text-sm leading-6"
    >
      <p className="font-semibold text-[var(--color-danger)]">
        扩大复核范围失败
      </p>
      <p className="mt-1 text-[var(--color-text)]">
        {error.message || "补充候选暂时不可用，当前主结果已保留。"}
      </p>
      <button
        type="button"
        className="mt-3 inline-flex h-9 items-center justify-center rounded-[8px] border border-[var(--color-danger)] bg-[var(--color-surface)] px-3 text-sm font-medium text-[var(--color-danger)] transition hover:bg-white focus:outline-none focus:ring-2 focus:ring-[var(--color-brand-soft)]"
        onClick={onRetry}
      >
        重试扩大复核范围
      </button>
    </div>
  );
}

function LowConfidenceResults({
  response,
  candidates,
  onSelectResult,
  getFeedbackState,
  onFeedback,
  getFavoriteSelection,
  getListSelection,
}: {
  response: SearchResponse;
  candidates: SearchResultItem[];
  onSelectResult: (result: SearchResultItem, triggerElement: HTMLElement) => void;
  getFeedbackState?: (result: SearchResultItem) => ResultFeedbackState | undefined;
  onFeedback?: (result: SearchResultItem, value: FeedbackSelection) => void;
  getFavoriteSelection?: (result: SearchResultItem) => FavoriteSelectionState | undefined;
  getListSelection?: (result: SearchResultItem) => ListSelectionState | undefined;
}) {
  const reasonTitle =
    response.coverage.search_mode === "expanded" ? "补充候选降级原因" : "候选降级原因";

  return (
    <div className="mt-4 space-y-3">
      {response.degraded_reasons.length > 0 ? (
        <div className="rounded-[8px] border border-[var(--color-border)] bg-white px-3 py-2 text-xs leading-5 text-[var(--color-text-muted)]">
          <p className="font-medium text-[var(--color-text)]">
            {reasonTitle}
          </p>
          <ul className="mt-1 list-inside list-disc">
            {response.degraded_reasons.map((reason) => (
              <li key={reason}>{formatDegradedReason(reason)}</li>
            ))}
          </ul>
        </div>
      ) : null}

      {candidates.length > 0 ? (
        <div aria-label="低置信度候选列表" className="space-y-3">
          {candidates.map((candidate, index) => (
            <ResultCard
              key={`${candidate.case_id}-${
                candidate.top_chunk_id || candidate.chunk_id || index
              }`}
              result={candidate}
              index={index}
              variant="lowConfidence"
              onSelect={onSelectResult}
              feedback={getFeedbackState?.(candidate)}
              onFeedback={onFeedback}
              favoriteSelection={getFavoriteSelection?.(candidate)}
              listSelection={getListSelection?.(candidate)}
            />
          ))}
        </div>
      ) : (
        <div className="rounded-[8px] border border-[var(--color-border)] bg-white px-3 py-3 text-sm leading-6 text-[var(--color-text-muted)]">
          暂未发现更多候选案例。可以补充行为经过、损害结果或争议焦点后重新检索。
        </div>
      )}
    </div>
  );
}

function filterExpandedCandidates(
  expandedResults: SearchResultItem[],
  primaryResults: SearchResultItem[]
) {
  const primaryCaseIds = new Set(primaryResults.map((result) => result.case_id));
  const seenCaseIds = new Set<string>();

  return expandedResults.filter((result) => {
    if (primaryCaseIds.has(result.case_id) || seenCaseIds.has(result.case_id)) {
      return false;
    }

    seenCaseIds.add(result.case_id);
    return true;
  });
}

function getLowConfidenceCandidates(
  response: SearchResponse,
  primaryResults: SearchResultItem[]
) {
  const separatedCandidates = response.low_confidence_candidates || [];
  if (separatedCandidates.length > 0) {
    return dedupeAgainstPrimary(separatedCandidates, primaryResults);
  }

  return filterExpandedCandidates(response.results, primaryResults);
}

function dedupeAgainstPrimary(
  candidates: SearchResultItem[],
  primaryResults: SearchResultItem[]
) {
  const primaryCaseIds = new Set(primaryResults.map((result) => result.case_id));
  const seenCaseIds = new Set<string>();

  return candidates.filter((result) => {
    if (primaryCaseIds.has(result.case_id) || seenCaseIds.has(result.case_id)) {
      return false;
    }

    seenCaseIds.add(result.case_id);
    return true;
  });
}
