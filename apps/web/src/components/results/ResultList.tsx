import { EmptyResults } from "../feedback/EmptyResults";
import { ResultSkeleton } from "../feedback/ResultSkeleton";
import { LowConfidencePanel } from "./LowConfidencePanel";
import {
  ResultCard,
  type CompareSelectionState,
  type FavoriteSelectionState,
  type ResultFeedbackState,
} from "./ResultCard";
import type { ListSelectionState } from "./AddToListButton";
import type { FeedbackSelection } from "../../services/feedbackApi";
import type {
  SearchCasesResult,
  SearchResponse,
  SearchResultItem,
} from "../../types/search";

type ResultListProps = {
  response?: SearchResponse;
  expandResult?: SearchCasesResult | null;
  isExpandLoading?: boolean;
  expandError?: Error | null;
  expandedSearchEnabled?: boolean;
  isLoading: boolean;
  onEdit: () => void;
  onExpand: () => void;
  onSelectResult: (result: SearchResultItem, triggerElement: HTMLElement) => void;
  getFeedbackState?: (result: SearchResultItem) => ResultFeedbackState | undefined;
  onFeedback?: (result: SearchResultItem, value: FeedbackSelection) => void;
  getCompareSelection?: (result: SearchResultItem) => CompareSelectionState | undefined;
  getFavoriteSelection?: (result: SearchResultItem) => FavoriteSelectionState | undefined;
  getListSelection?: (result: SearchResultItem) => ListSelectionState | undefined;
};

export function ResultList({
  response,
  expandResult,
  isExpandLoading = false,
  expandError,
  expandedSearchEnabled = false,
  isLoading,
  onEdit,
  onExpand,
  onSelectResult,
  getFeedbackState,
  onFeedback,
  getCompareSelection,
  getFavoriteSelection,
  getListSelection,
}: ResultListProps) {
  if (isLoading) {
    return <ResultSkeleton />;
  }

  if (!response) {
    return null;
  }

  const results = response.results;
  const lowConfidenceResponse = buildLowConfidenceResponse(response, expandResult);
  const shouldShowExpandEntry = expandedSearchEnabled && results.length < 5;

  if (results.length === 0) {
    return (
      <div className="space-y-3">
        <EmptyResults
          onEdit={onEdit}
          onExpand={onExpand}
          canExpand={expandedSearchEnabled}
          isExpandLoading={isExpandLoading}
        />
        <LowConfidencePanel
          primaryResults={results}
          response={lowConfidenceResponse}
          source={expandResult?.source}
          isLoading={isExpandLoading}
          error={expandError}
          showEntry={false}
          onExpand={onExpand}
          onSelectResult={onSelectResult}
          getFeedbackState={getFeedbackState}
          onFeedback={onFeedback}
          getFavoriteSelection={getFavoriteSelection}
          getListSelection={getListSelection}
        />
      </div>
    );
  }

  return (
    <section aria-label="搜索结果列表" className="space-y-3">
      <div className="space-y-3">
        {results.map((result, index) => (
          <ResultCard
            key={`${result.case_id}-${result.top_chunk_id || result.chunk_id || index}`}
            result={result}
            index={index}
            onSelect={onSelectResult}
            feedback={getFeedbackState?.(result)}
            onFeedback={onFeedback}
            compareSelection={getCompareSelection?.(result)}
            favoriteSelection={getFavoriteSelection?.(result)}
            listSelection={getListSelection?.(result)}
          />
        ))}
      </div>
      <LowConfidencePanel
        primaryResults={results}
        response={lowConfidenceResponse}
        source={expandResult?.source}
        isLoading={isExpandLoading}
        error={expandError}
        showEntry={shouldShowExpandEntry}
        onExpand={onExpand}
        onSelectResult={onSelectResult}
        getFeedbackState={getFeedbackState}
        onFeedback={onFeedback}
        getFavoriteSelection={getFavoriteSelection}
        getListSelection={getListSelection}
      />
    </section>
  );
}

function buildLowConfidenceResponse(
  response: SearchResponse,
  expandResult?: SearchCasesResult | null
) {
  const standardCandidates = response.low_confidence_candidates || [];
  const expandedCandidates = expandResult?.response
    ? getResponseLowConfidenceCandidates(expandResult.response, [
        ...response.results,
        ...standardCandidates,
      ])
    : [];
  const mergedCandidates = dedupeCandidates([
    ...standardCandidates,
    ...expandedCandidates,
  ]);

  if (mergedCandidates.length === 0 && !expandResult?.response) {
    return undefined;
  }

  return {
    ...(expandResult?.response || response),
    results: [],
    low_confidence_candidates: mergedCandidates,
  };
}

function getResponseLowConfidenceCandidates(
  response: SearchResponse,
  existingResults: SearchResultItem[]
) {
  const separated = response.low_confidence_candidates || [];
  const candidates = separated.length > 0 ? separated : response.results;
  const existingCaseIds = new Set(existingResults.map((result) => result.case_id));
  return candidates.filter((candidate) => !existingCaseIds.has(candidate.case_id));
}

function dedupeCandidates(candidates: SearchResultItem[]) {
  const seen = new Set<string>();
  return candidates.filter((candidate) => {
    if (seen.has(candidate.case_id)) {
      return false;
    }

    seen.add(candidate.case_id);
    return true;
  });
}
