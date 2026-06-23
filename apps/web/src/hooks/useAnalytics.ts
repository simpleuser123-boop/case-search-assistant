import { useCallback, useMemo } from "react";

import {
  trackCaseDetailView,
  trackExtendedSearchTrigger,
  trackPageExit,
  trackResultCardClick,
  trackSearchRefine,
  trackSearchResultRender,
  trackSearchSubmit,
  trackSearchZeroResult,
  type CaseDetailViewAnalyticsPayload,
  type ExtendedSearchTriggerAnalyticsPayload,
  type PageExitAnalyticsPayload,
  type ResultCardClickAnalyticsPayload,
  type SearchRefineAnalyticsPayload,
  type SearchResultRenderAnalyticsPayload,
  type SearchSubmitAnalyticsPayload,
  type SearchZeroResultAnalyticsPayload,
} from "../services/analytics";

export function useAnalytics() {
  const submit = useCallback(
    (payload: SearchSubmitAnalyticsPayload) => trackSearchSubmit(payload),
    []
  );
  const resultRender = useCallback(
    (payload: SearchResultRenderAnalyticsPayload) =>
      trackSearchResultRender(payload),
    []
  );
  const resultCardClick = useCallback(
    (payload: ResultCardClickAnalyticsPayload) => trackResultCardClick(payload),
    []
  );
  const caseDetailView = useCallback(
    (payload: CaseDetailViewAnalyticsPayload) => trackCaseDetailView(payload),
    []
  );
  const searchRefine = useCallback(
    (payload: SearchRefineAnalyticsPayload) => trackSearchRefine(payload),
    []
  );
  const zeroResult = useCallback(
    (payload: SearchZeroResultAnalyticsPayload) => trackSearchZeroResult(payload),
    []
  );
  const extendedSearchTrigger = useCallback(
    (payload: ExtendedSearchTriggerAnalyticsPayload) =>
      trackExtendedSearchTrigger(payload),
    []
  );
  const pageExit = useCallback(
    (payload: PageExitAnalyticsPayload) => trackPageExit(payload),
    []
  );

  return useMemo(
    () => ({
      trackSearchSubmit: submit,
      trackSearchResultRender: resultRender,
      trackResultCardClick: resultCardClick,
      trackCaseDetailView: caseDetailView,
      trackSearchRefine: searchRefine,
      trackSearchZeroResult: zeroResult,
      trackExtendedSearchTrigger: extendedSearchTrigger,
      trackPageExit: pageExit,
    }),
    [
      caseDetailView,
      extendedSearchTrigger,
      pageExit,
      resultCardClick,
      resultRender,
      searchRefine,
      submit,
      zeroResult,
    ]
  );
}
