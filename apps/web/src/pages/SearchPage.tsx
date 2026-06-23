import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation } from "react-router-dom";

import { CaseDetailDrawer } from "../components/details/CaseDetailDrawer";
import { CaseCompareView } from "../components/results/CaseCompareView";
import { ErrorBanner } from "../components/feedback/ErrorBanner";
import { ResultList } from "../components/results/ResultList";
import { ResultOverview } from "../components/results/ResultOverview";
import { RiskHintsPanel } from "../components/results/RiskHintsPanel";
import { SearchHistoryPanel } from "../components/search/SearchHistoryPanel";
import { MAX_COMPARE_CASES, MIN_COMPARE_CASES } from "../lib/caseCompare";
import {
  appendHistory,
  clearDraft as clearDraftStorage,
  clearHistory as clearHistoryStorage,
  getBrowserLocalStorage,
  loadDraft,
  loadHistory,
  removeHistoryEntry,
  saveDraft,
  type SearchHistoryEntry,
  type StorageLike,
} from "../lib/searchHistory";
import {
  clearFavorites as clearFavoritesStorage,
  getBrowserLocalStorage as getFavoriteLocalStorage,
  loadFavorites,
  removeFavorite,
  toggleFavorite,
  updateFavoriteFields,
  type CaseFavoriteRecord,
  type FavoriteMetadataSource,
  type StorageLike as FavoriteStorageLike,
} from "../lib/caseFavorite";
import { FavoritesPanel } from "../components/results/FavoritesPanel";
import { CaseListPanel } from "../components/results/CaseListPanel";
import {
  addItemToList,
  createList,
  deleteList,
  getBrowserLocalStorage as getCaseListLocalStorage,
  listIdsContainingCase,
  loadLists,
  moveListItem,
  removeItemFromList,
  renameList,
  updateListItemFields,
  type CaseListItem,
  type CaseListRecord,
  type ListItemMetadataSource,
  type StorageLike as CaseListStorageLike,
} from "../lib/caseList";
import {
  exportCaseList,
  logCaseListExport,
  type ExportFormat,
} from "../lib/caseListExport";
import {
  buildReportTemplate,
  downloadReport,
  logReportTemplate,
  type ReportTemplate,
} from "../lib/reportTemplate";
import type { ListSelectionState } from "../components/results/AddToListButton";
import {
  isCaseFavoriteEnabled,
  isCaseListEnabled,
  isExpandedSearchEnabled,
  isListExportEnabled,
  isReportTemplateEnabled,
  isSearchHistoryEnabled,
  isStatuteSearchEnabled,
  isDraftingEnabled,
  isCasebookEnabled,
} from "../config/featureFlags";
import { useAnalytics } from "../hooks/useAnalytics";
import { useExpandSearchCases, useSearchCases } from "../hooks/useSearchCases";
import { type SearchTrigger, validateSearchInput } from "../lib/searchValidation";
import {
  type FeedbackConfidenceLevel,
  type FeedbackSearchMode,
  type FeedbackSelection,
  submitFeedbackEvent,
} from "../services/feedbackApi";
import type {
  CaseDetailResponse,
  SearchCasesResult,
  SearchResultItem,
} from "../types/search";

type SearchLocationState = {
  query?: string;
  inputLength?: number;
};

type SelectedDetailState = {
  result: SearchResultItem;
  querySessionId?: string | null;
  rank: number;
};

type FeedbackUiState = {
  value: FeedbackSelection | null;
  isPending: boolean;
  error: boolean;
};

type FeedbackContext = {
  key: string;
  querySessionId: string;
  rank: number;
  searchMode: FeedbackSearchMode;
  confidenceLevel: FeedbackConfidenceLevel;
};

export function SearchPage() {
  const location = useLocation();
  const analytics = useAnalytics();
  const state = location.state as SearchLocationState | null;
  const initialQuery = state?.query || "";
  const [query, setQuery] = useState(initialQuery);
  const [submittedQuery, setSubmittedQuery] = useState("");
  const [showValidation, setShowValidation] = useState(false);
  const [lastResult, setLastResult] = useState<SearchCasesResult | null>(null);
  const [expandResult, setExpandResult] = useState<SearchCasesResult | null>(null);
  const [selectedDetailResult, setSelectedDetailResult] =
    useState<SelectedDetailState | null>(null);
  const [resultFeedback, setResultFeedback] = useState<Record<string, FeedbackUiState>>({});
  // M3-6: ephemeral case-compare selection. Lives only in React state, never
  // persisted, never restored across sessions, never used to alter ranking.
  const [compareCaseIds, setCompareCaseIds] = useState<string[]>([]);
  const [isCompareOpen, setIsCompareOpen] = useState(false);
  const hasAutoSubmittedRef = useRef(false);
  const searchInputRef = useRef<HTMLTextAreaElement | null>(null);
  const detailTriggerRef = useRef<HTMLElement | null>(null);
  const activeExpandQueryRef = useRef("");
  const activeQuerySessionIdRef = useRef<string | null>(null);
  const lastVisibleResultCountRef = useRef(0);
  const pageExitTrackedRef = useRef(false);
  const refineCountRef = useRef(0);
  const pageEnteredAtRef = useRef(Date.now());
  const searchMutation = useSearchCases();
  const expandMutation = useExpandSearchCases();
  const expandedSearchEnabled = isExpandedSearchEnabled();
  // E5-5：类案结果页「跳法条检索」入口受 VITE_ENABLE_STATUTE_SEARCH 门控；默认 off 不渲染。
  const statuteSearchEnabled = isStatuteSearchEnabled();
  const draftingEnabled = isDraftingEnabled();
  // E7-3：类案结果页「协作工作台」入口受 VITE_ENABLE_CASEBOOK 门控；默认 off 不渲染。
  const casebookEnabled = isCasebookEnabled();

  // M4-2 检索历史与草稿恢复（F16），flag 默认 false。关闭时本块完全惰性：
  // 不读写任何本地存储、不渲染历史/草稿入口，页面回到 M3 末态。
  const historyEnabled = isSearchHistoryEnabled();
  const localStorageRef = useRef<StorageLike | null | undefined>(undefined);
  const getLocalStorage = (): StorageLike | null => {
    if (localStorageRef.current === undefined) {
      localStorageRef.current = historyEnabled ? getBrowserLocalStorage() : null;
    }
    return localStorageRef.current;
  };
  const [historyEntries, setHistoryEntries] = useState<SearchHistoryEntry[]>([]);
  const [hasDraft, setHasDraft] = useState(false);
  const [draftRestored, setDraftRestored] = useState(false);
  // 本次提交是否由草稿恢复触发——仅用于已在白名单内的 has_draft_restored 脱敏埋点。
  const draftRestoredForSubmitRef = useRef(false);
  const historyInitDoneRef = useRef(false);
  // 记录被恢复的草稿正文：自动保存 effect 据此判断是「保持恢复态」还是用户已改写。
  const restoredDraftTextRef = useRef<string | null>(null);

  // M4-3 案例收藏（F17），flag 默认 false。关闭时本块完全惰性：不读写本地存储、
  // 不渲染收藏入口/列表，页面回到 M4-2 末态。收藏只存元数据/锚点/用户自填短字段，
  // 仅本浏览器、可清除；不上送后端、不参与主排序。
  const favoriteEnabled = isCaseFavoriteEnabled();
  const favoriteStorageRef = useRef<FavoriteStorageLike | null | undefined>(undefined);
  const getFavoriteStorage = (): FavoriteStorageLike | null => {
    if (favoriteStorageRef.current === undefined) {
      favoriteStorageRef.current = favoriteEnabled ? getFavoriteLocalStorage() : null;
    }
    return favoriteStorageRef.current;
  };
  const [favorites, setFavorites] = useState<CaseFavoriteRecord[]>([]);
  const favoriteInitDoneRef = useRef(false);

  useEffect(() => {
    if (!favoriteEnabled || favoriteInitDoneRef.current) {
      return;
    }
    favoriteInitDoneRef.current = true;
    const storage = getFavoriteStorage();
    if (storage) {
      setFavorites(loadFavorites(storage));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [favoriteEnabled]);

  const favoritedCaseIds = useMemo(
    () => new Set(favorites.map((entry) => entry.case_id)),
    [favorites]
  );

  // M4-4 类案清单（F17），flag 默认 false。关闭时本块完全惰性：不读写本地存储、
  // 不渲染清单入口/面板，页面回到 M4-3 末态。清单只存引用/元数据/锚点/用户自填短
  // 字段，仅本浏览器、可清除；不上送后端、不参与主排序/召回/source selection。
  const listEnabled = isCaseListEnabled();
  const listExportEnabled = listEnabled && isListExportEnabled();
  const reportEnabled = listEnabled && isReportTemplateEnabled();
  const listStorageRef = useRef<CaseListStorageLike | null | undefined>(undefined);
  const getListStorage = (): CaseListStorageLike | null => {
    if (listStorageRef.current === undefined) {
      listStorageRef.current = listEnabled ? getCaseListLocalStorage() : null;
    }
    return listStorageRef.current;
  };
  const [caseLists, setCaseLists] = useState<CaseListRecord[]>([]);
  const listInitDoneRef = useRef(false);

  useEffect(() => {
    if (!listEnabled || listInitDoneRef.current) {
      return;
    }
    listInitDoneRef.current = true;
    const storage = getListStorage();
    if (storage) {
      setCaseLists(loadLists(storage));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [listEnabled]);

  // 初始化：仅在 flag 开启时，加载本地历史并恢复草稿（首页已带 query 时不覆盖）。
  useEffect(() => {
    if (!historyEnabled || historyInitDoneRef.current) {
      return;
    }
    historyInitDoneRef.current = true;
    const storage = getLocalStorage();
    if (!storage) {
      return;
    }
    setHistoryEntries(loadHistory(storage));
    if (!initialQuery) {
      const draft = loadDraft(storage);
      if (draft) {
        setQuery(draft.draft_text);
        setHasDraft(true);
        setDraftRestored(true);
        draftRestoredForSubmitRef.current = true;
        restoredDraftTextRef.current = draft.draft_text;
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [historyEnabled]);

  // 草稿自动保存：仅 flag 开启时生效。已提交且与提交内容一致的输入不再当作草稿
  // （检索成功后清空草稿）；空白输入清除草稿。草稿正文只落浏览器本地。
  useEffect(() => {
    if (!historyEnabled || !historyInitDoneRef.current) {
      return;
    }
    const storage = getLocalStorage();
    if (!storage) {
      return;
    }
    const trimmed = query.trim();
    if (!trimmed || query === submittedQuery) {
      clearDraftStorage(storage);
      setHasDraft(false);
      return;
    }
    const saved = saveDraft(storage, query);
    setHasDraft(saved);
    // 仍等于被恢复的草稿正文时保持「已恢复」态；用户一旦改写即视为新草稿。
    if (restoredDraftTextRef.current !== null && query === restoredDraftTextRef.current) {
      setDraftRestored(true);
    } else {
      setDraftRestored(false);
      restoredDraftTextRef.current = null;
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query, submittedQuery, historyEnabled]);

  const validation = useMemo(() => validateSearchInput(query), [query]);
  const errorMessage =
    (showValidation || Boolean(query)) && !validation.isValid
      ? validation.message
      : "";
  const isSearching = searchMutation.isPending;
  const canSubmit = validation.isValid && !isSearching;
  const resultResponse = lastResult?.response;
  const resultSource = lastResult?.source;
  const lastVisibleResultCount =
    (resultResponse?.results.length ?? 0) +
    (resultResponse?.low_confidence_candidates.length ?? 0) +
    (expandResult?.response.results.length ?? 0) +
    (expandResult?.response.low_confidence_candidates.length ?? 0);
  const activeQuerySessionId =
    expandResult?.response.query_session_id ?? resultResponse?.query_session_id ?? null;

  useEffect(() => {
    activeQuerySessionIdRef.current = activeQuerySessionId;
  }, [activeQuerySessionId]);

  useEffect(() => {
    lastVisibleResultCountRef.current = lastVisibleResultCount;
  }, [lastVisibleResultCount]);

  useEffect(() => {
    function handlePageExit() {
      if (pageExitTrackedRef.current || !activeQuerySessionIdRef.current) {
        return;
      }

      pageExitTrackedRef.current = true;
      void analytics.trackPageExit({
        query_session_id: activeQuerySessionIdRef.current,
        last_visible_result_count: lastVisibleResultCountRef.current,
        dwell_time_ms: Date.now() - pageEnteredAtRef.current,
      });
    }

    window.addEventListener("pagehide", handlePageExit);
    return () => {
      handlePageExit();
      window.removeEventListener("pagehide", handlePageExit);
    };
  }, [analytics]);

  useEffect(() => {
    if (hasAutoSubmittedRef.current || !initialQuery) {
      return;
    }

    const autoSubmitTimer = window.setTimeout(() => {
      hasAutoSubmittedRef.current = true;
      runSearch(initialQuery, { trackSubmit: false });
    }, 0);

    return () => window.clearTimeout(autoSubmitTimer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialQuery]);

  function runSearch(
    rawQuery = query,
    options: {
      trackSubmit?: boolean;
      useMock?: boolean;
      trigger?: SearchTrigger;
    } = {}
  ) {
    const nextValidation = validateSearchInput(rawQuery);
    setQuery(rawQuery);

    if (!nextValidation.isValid) {
      setShowValidation(true);
      return;
    }

    const cleaned = nextValidation.cleaned;
    setShowValidation(false);
    const previousQuerySessionId = resultResponse?.query_session_id;
    const previousResultCount = resultResponse?.results.length ?? 0;
    const isRefine =
      Boolean(previousQuerySessionId) &&
      Boolean(submittedQuery) &&
      cleaned !== submittedQuery;

    setSubmittedQuery(cleaned);
    setExpandResult(null);
    setSelectedDetailResult(null);
    setResultFeedback({});
    setCompareCaseIds([]);
    setIsCompareOpen(false);
    detailTriggerRef.current = null;
    activeExpandQueryRef.current = "";
    expandMutation.reset();

    if (options.trackSubmit !== false) {
      void analytics.trackSearchSubmit({
        query_session_id: previousQuerySessionId,
        input_length: Array.from(cleaned).length,
        trigger: options.trigger ?? "button",
        has_draft_restored: historyEnabled && draftRestoredForSubmitRef.current,
      });

      if (isRefine && previousQuerySessionId) {
        refineCountRef.current += 1;
        void analytics.trackSearchRefine({
          query_session_id: previousQuerySessionId,
          refine_count: refineCountRef.current,
          previous_result_count: previousResultCount,
          input_length: Array.from(cleaned).length,
        });
      }
    }

    searchMutation.mutate(
      { query: cleaned, limit: 10, useMock: options.useMock },
      {
        onSuccess: (data) => {
          setLastResult(data);
          void analytics.trackSearchResultRender({
            query_session_id: data.response.query_session_id,
            result_count: data.response.results.length,
            degraded: data.response.degraded,
            total_duration_ms: data.response.timings.total_duration_ms,
            degraded_reason_count: data.response.degraded_reasons.length,
          });

          // M4-2: 检索成功后写入本地历史并清除草稿（均仅本地、flag 开启时生效）。
          // result_count 取主结果 + 低置信候选，与可见结果口径一致；正文不上送。
          if (historyEnabled) {
            const storage = getLocalStorage();
            if (storage) {
              const visibleCount =
                data.response.results.length +
                data.response.low_confidence_candidates.length;
              setHistoryEntries((previous) =>
                appendHistory(storage, previous, {
                  query_text: cleaned,
                  input_length: Array.from(cleaned).length,
                  result_count: visibleCount,
                  degraded: data.response.degraded,
                })
              );
              clearDraftStorage(storage);
              setHasDraft(false);
              setDraftRestored(false);
            }
          }

          if (data.response.results.length === 0) {
            void analytics.trackSearchZeroResult({
              query_session_id: data.response.query_session_id,
              input_length: Array.from(cleaned).length,
              fallback_available: true,
            });
          }
        },
      }
    );

    draftRestoredForSubmitRef.current = false;
  }

  function handleExpandSearch() {
    if (!expandedSearchEnabled) {
      return;
    }

    const baseQuery = submittedQuery || query;
    const nextValidation = validateSearchInput(baseQuery);

    if (!nextValidation.isValid) {
      setShowValidation(true);
      searchInputRef.current?.focus();
      return;
    }

    const cleaned = nextValidation.cleaned;
    const querySessionId = resultResponse?.query_session_id;
    setShowValidation(false);
    activeExpandQueryRef.current = cleaned;

    if (querySessionId) {
      void analytics.trackExtendedSearchTrigger({
        query_session_id: querySessionId,
        main_result_count: resultResponse?.results.length ?? 0,
      });
    }

    expandMutation.mutate(
      {
        query: cleaned,
        limit: 10,
        useMock: resultSource === "mock",
      },
      {
        onSuccess: (data) => {
          if (activeExpandQueryRef.current === cleaned) {
            setExpandResult(data);
          }
        },
      }
    );
  }

  function handleSelectResult(
    result: SearchResultItem,
    triggerElement: HTMLElement
  ) {
    const primaryRank =
      resultResponse?.results.findIndex((item) => item.case_id === result.case_id) ??
      -1;
    const lowConfidenceRank =
      resultResponse?.low_confidence_candidates.findIndex(
        (item) => item.case_id === result.case_id
      ) ?? -1;
    const expandCandidates = [
      ...(expandResult?.response.results ?? []),
      ...(expandResult?.response.low_confidence_candidates ?? []),
    ];
    const expandRank =
      expandCandidates.findIndex((item) => item.case_id === result.case_id) ?? -1;
    const querySessionId =
      primaryRank >= 0
        ? resultResponse?.query_session_id
        : lowConfidenceRank >= 0
          ? resultResponse?.query_session_id
          : expandRank >= 0
          ? expandResult?.response.query_session_id
          : resultResponse?.query_session_id;
    const rank =
      primaryRank >= 0
        ? primaryRank + 1
        : lowConfidenceRank >= 0
          ? lowConfidenceRank + 1
          : expandRank >= 0
            ? expandRank + 1
            : 0;
    detailTriggerRef.current = triggerElement;
    setSelectedDetailResult({
      result,
      querySessionId,
      rank,
    });
    void analytics.trackResultCardClick({
      query_session_id: querySessionId,
      case_id: result.case_id,
      rank,
      similarity_score:
        result.final_score ?? result.similarity_score ?? result.retrieval_score ?? null,
    });
  }

  function handleCloseDetail() {
    const trigger = detailTriggerRef.current;
    setSelectedDetailResult(null);
    window.setTimeout(() => trigger?.focus({ preventScroll: true }), 0);
  }

  // M4-2: 从历史重搜。把历史正文回填后走与首次检索完全相同的 runSearch 链路
  // （清洗 / 改写降级 / 主排序默认均不变）。历史不参与、不改变主排序，
  // 也不按 query/case id 特判。
  function handleResearchFromHistory(entry: SearchHistoryEntry) {
    draftRestoredForSubmitRef.current = false;
    runSearch(entry.query_text, { trigger: "button" });
  }

  function handleRemoveHistoryEntry(id: string) {
    const storage = getLocalStorage();
    if (!storage) {
      return;
    }
    setHistoryEntries((previous) => removeHistoryEntry(storage, previous, id));
  }

  function handleClearHistory() {
    const storage = getLocalStorage();
    if (storage) {
      clearHistoryStorage(storage);
    }
    setHistoryEntries([]);
  }

  function handleClearDraft() {
    const storage = getLocalStorage();
    if (storage) {
      clearDraftStorage(storage);
    }
    setHasDraft(false);
    setDraftRestored(false);
    draftRestoredForSubmitRef.current = false;
    setQuery("");
  }

  // M3-6: the pool a user may pick from is strictly the *current* visible
  // results (primary + low-confidence + expanded). Selection never leaves this
  // pool and is dropped on every new search.
  const compareSelectablePool = useMemo(() => {
    const pool = new Map<string, SearchResultItem>();
    [
      ...(resultResponse?.results ?? []),
      ...(resultResponse?.low_confidence_candidates ?? []),
      ...(expandResult?.response.results ?? []),
      ...(expandResult?.response.low_confidence_candidates ?? []),
    ].forEach((item) => {
      if (!pool.has(item.case_id)) {
        pool.set(item.case_id, item);
      }
    });
    return pool;
  }, [resultResponse, expandResult]);

  const compareSelectedResults = useMemo(
    () =>
      compareCaseIds
        .map((caseId) => compareSelectablePool.get(caseId))
        .filter((item): item is SearchResultItem => Boolean(item)),
    [compareCaseIds, compareSelectablePool]
  );

  const compareRiskHints = useMemo(
    () => [
      ...(resultResponse?.risk_hints ?? []),
      ...(expandResult?.response.risk_hints ?? []),
    ],
    [resultResponse, expandResult]
  );

  // Drop any selected id that is no longer in the visible pool so selection can
  // never outlive the results it was made from.
  useEffect(() => {
    setCompareCaseIds((previous) => {
      const pruned = previous.filter((caseId) => compareSelectablePool.has(caseId));
      return pruned.length === previous.length ? previous : pruned;
    });
  }, [compareSelectablePool]);

  function handleToggleCompare(result: SearchResultItem) {
    setCompareCaseIds((previous) => {
      if (previous.includes(result.case_id)) {
        return previous.filter((caseId) => caseId !== result.case_id);
      }
      if (previous.length >= MAX_COMPARE_CASES) {
        return previous;
      }
      return [...previous, result.case_id];
    });
  }

  function getCompareSelection(result: SearchResultItem) {
    const checked = compareCaseIds.includes(result.case_id);
    return {
      checked,
      disabled: compareCaseIds.length >= MAX_COMPARE_CASES && !checked,
      onToggle: handleToggleCompare,
    };
  }

  // M4-3: 切换收藏。只从已在屏的元数据来源构造记录（元数据 + 锚点 + 短字段），
  // 绝不写入任何正文；收藏只落本地、不参与主排序、不按 case id 特判。
  function handleToggleFavorite(source: FavoriteMetadataSource) {
    if (!favoriteEnabled) {
      return;
    }
    const storage = getFavoriteStorage();
    if (!storage) {
      return;
    }
    setFavorites((previous) => toggleFavorite(storage, previous, source).entries);
  }

  function handleRemoveFavorite(caseId: string) {
    const storage = getFavoriteStorage();
    if (!storage) {
      return;
    }
    setFavorites((previous) => removeFavorite(storage, previous, caseId));
  }

  function handleClearFavorites() {
    const storage = getFavoriteStorage();
    if (storage) {
      clearFavoritesStorage(storage);
    }
    setFavorites([]);
  }

  function handleUpdateFavoriteFields(
    caseId: string,
    fields: { note?: string; tag?: string }
  ) {
    const storage = getFavoriteStorage();
    if (!storage) {
      return;
    }
    setFavorites((previous) => updateFavoriteFields(storage, previous, caseId, fields));
  }

  // 结果卡片 / 低置信候选 / 对比视图共用的收藏切换状态。flag 关闭时返回 undefined，
  // 按钮不渲染。
  function getFavoriteSelection(result: SearchResultItem) {
    if (!favoriteEnabled) {
      return undefined;
    }
    return {
      favorited: favoritedCaseIds.has(result.case_id),
      onToggle: (item: SearchResultItem) => handleToggleFavorite(item),
    };
  }

  // ---------- M4-4 类案清单 handlers（均仅本地、白名单字段、零正文）----------

  // 切换某案例在某清单内的归属：已在则移出，未在则加入。
  function handleToggleListMembership(listId: string, source: ListItemMetadataSource) {
    if (!listEnabled) {
      return;
    }
    const storage = getListStorage();
    if (!storage) {
      return;
    }
    const caseId = (source.case_id || "").trim();
    setCaseLists((previous) => {
      const target = previous.find((entry) => entry.list_id === listId);
      const contained = target?.items.some((item) => item.case_id === caseId);
      return contained
        ? removeItemFromList(storage, previous, listId, caseId).lists
        : addItemToList(storage, previous, listId, source).lists;
    });
  }

  // 新建一张清单，并把当前案例作为首项加入。
  function handleCreateListWithCase(title: string, source: ListItemMetadataSource) {
    if (!listEnabled) {
      return;
    }
    const storage = getListStorage();
    if (!storage) {
      return;
    }
    setCaseLists((previous) => createList(storage, previous, title, source).lists);
  }

  function handleRemoveListItem(listId: string, caseId: string) {
    const storage = getListStorage();
    if (!storage) {
      return;
    }
    setCaseLists((previous) => removeItemFromList(storage, previous, listId, caseId).lists);
  }

  function handleMoveListItem(listId: string, caseId: string, direction: "up" | "down") {
    const storage = getListStorage();
    if (!storage) {
      return;
    }
    setCaseLists((previous) => moveListItem(storage, previous, listId, caseId, direction).lists);
  }

  function handleUpdateListItemFields(
    listId: string,
    caseId: string,
    fields: { note?: string; tag?: string }
  ) {
    const storage = getListStorage();
    if (!storage) {
      return;
    }
    setCaseLists((previous) => updateListItemFields(storage, previous, listId, caseId, fields).lists);
  }

  function handleRenameList(listId: string, title: string) {
    const storage = getListStorage();
    if (!storage) {
      return;
    }
    setCaseLists((previous) => renameList(storage, previous, listId, title).lists);
  }

  function handleDeleteList(listId: string) {
    const storage = getListStorage();
    if (!storage) {
      return;
    }
    setCaseLists((previous) => deleteList(storage, previous, listId));
  }

  // M4-5 清单导出：仅在浏览器本地生成下载，文件只含元数据 / 来源引用 / 用户自填
  // 备注 + 强制免责说明，绝不含正文，绝不上送后端、不参与主排序。导出失败安全降级，
  // 不影响主链路。返回结果供面板展示成功 / 降级状态。
  function handleExportList(listId: string, format: ExportFormat) {
    if (!listExportEnabled) {
      return { ok: false, status: "failed" as const };
    }
    const target = caseLists.find((entry) => entry.list_id === listId);
    const result = exportCaseList(target, { format });
    logCaseListExport(result.descriptor);
    return { ok: result.descriptor.export_status === "exported", status: result.descriptor.export_status };
  }

  // M4-6 轻量报告模板：基于清单在浏览器本地组装报告骨架（模板结构 + 元数据 + 来源
  // 锚点 + 用户自填备注 + 系统占位 + 免责说明）。不起草法律文书、不下胜负结论、
  // 不写入正文；AI 片段经守门，无锚点不进入。组装失败安全降级，不影响主链路。
  function handleGenerateReport(listId: string, backgroundNote: string): ReportTemplate {
    const target = reportEnabled
      ? caseLists.find((entry) => entry.list_id === listId)
      : undefined;
    const report = buildReportTemplate(target, { backgroundNote });
    logReportTemplate(report);
    return report;
  }

  // M4-6 报告导出：复用 M4-5 下载能力，仅本地生成 Markdown 文件。导出失败安全降级。
  function handleDownloadReport(report: ReportTemplate) {
    if (!reportEnabled) {
      return { ok: false, status: "failed" as const };
    }
    const result = downloadReport(report);
    logReportTemplate(result.report);
    return {
      ok: result.report.report_status === "generated" && result.content !== null,
      status: result.report.report_status,
    };
  }

  // 结果卡片 / 低置信候选 / 对比视图共用的清单选择状态。flag 关闭时返回 undefined，
  // 按钮不渲染。只读「本案在哪些清单内」的引用关系，绝不回写排序特征。
  function getListSelection(result: SearchResultItem): ListSelectionState | undefined {
    if (!listEnabled) {
      return undefined;
    }
    const source: ListItemMetadataSource = {
      case_id: result.case_id,
      case_no: result.case_no,
      court: result.court,
      trial_level: result.trial_level,
      court_level: result.court_level,
      case_cause: result.case_cause,
      judgment_date: result.judgment_date,
      source_anchors: result.source_anchors,
    };
    const containing = new Set(listIdsContainingCase(caseLists, result.case_id));
    return {
      choices: caseLists.map((entry) => ({
        list_id: entry.list_id,
        list_title: entry.list_title,
        contains: containing.has(entry.list_id),
        item_count: entry.items.length,
      })),
      inCount: containing.size,
      onToggleList: (listId: string) => handleToggleListMembership(listId, source),
      onCreateAndAdd: (title: string) => handleCreateListWithCase(title, source),
    };
  }

  // M4-4: 从清单回跳案例详情。仅用清单项里的 case_id 与元数据构造最小 seed（无正文）。
  function handleOpenListItemDetail(item: CaseListItem) {
    const seed: SearchResultItem = {
      case_id: item.case_id,
      source_chunk_ids: item.source_anchors.map((a) => a.source_chunk_id),
      source_anchors: item.source_anchors.map((a) => ({
        case_id: a.case_id,
        source_chunk_id: a.source_chunk_id,
        anchor_type: a.anchor_type || "case_record",
        chunk_type: a.chunk_type ?? null,
      })),
      hit_chunk_ids: [],
      retrieval_source: [],
      score_breakdown: {},
      case_no: item.case_number || null,
      court: item.court || null,
      trial_level: item.trial_level || null,
      case_cause: item.case_cause || null,
      judgment_date: item.judgment_date || null,
      highlights: [],
      metadata: {},
    };
    detailTriggerRef.current = null;
    setSelectedDetailResult({
      result: seed,
      querySessionId: activeQuerySessionIdRef.current,
      rank: 0,
    });
  }

  // 详情抽屉收藏状态：优先用 detail 字段，缺失回退 seed 结果，仍只取元数据。
  function getDetailFavoriteSelection() {
    if (!favoriteEnabled || !selectedDetailResult) {
      return undefined;
    }
    const caseId = selectedDetailResult.result.case_id;
    return {
      favorited: favoritedCaseIds.has(caseId),
      onToggle: (detail: CaseDetailResponse, seed?: SearchResultItem) => {
        handleToggleFavorite({
          case_id: detail.case_id || seed?.case_id || caseId,
          case_no: detail.case_no ?? seed?.case_no,
          court: detail.court ?? seed?.court,
          trial_level: detail.trial_level ?? seed?.trial_level,
          court_level: detail.court_level ?? seed?.court_level,
          case_cause: detail.case_cause ?? seed?.case_cause,
          judgment_date: detail.judgment_date ?? seed?.judgment_date,
          source_anchors: detail.chunks?.length
            ? detail.chunks.flatMap((chunk) => chunk.source_anchors ?? [])
            : seed?.source_anchors,
        });
      },
    };
  }

  // M4-3: 从收藏列表回跳案例详情。仅用收藏记录里的 case_id 与元数据构造一个最小
  // seed（无正文），交给与结果卡片一致的详情抽屉打开。
  function handleOpenFavoriteDetail(record: CaseFavoriteRecord) {
    const seed: SearchResultItem = {
      case_id: record.case_id,
      source_chunk_ids: record.source_anchors.map((a) => a.source_chunk_id),
      source_anchors: record.source_anchors.map((a) => ({
        case_id: a.case_id,
        source_chunk_id: a.source_chunk_id,
        anchor_type: a.anchor_type || "case_record",
        chunk_type: a.chunk_type ?? null,
      })),
      hit_chunk_ids: [],
      retrieval_source: [],
      score_breakdown: {},
      case_no: record.case_number || null,
      court: record.court || null,
      trial_level: record.trial_level || null,
      case_cause: record.case_cause || null,
      judgment_date: record.judgment_date || null,
      highlights: [],
      metadata: {},
    };
    detailTriggerRef.current = null;
    setSelectedDetailResult({
      result: seed,
      querySessionId: activeQuerySessionIdRef.current,
      rank: 0,
    });
  }

  function handleFeedback(result: SearchResultItem, selection: FeedbackSelection) {
    const context = resolveFeedbackContext(result);
    const rawQuery = submittedQuery || query;

    if (!context || !rawQuery.trim()) {
      return;
    }

    const currentValue = resultFeedback[context.key]?.value;
    const feedbackValue = currentValue === selection ? "cleared" : selection;
    const nextValue = feedbackValue === "cleared" ? null : selection;

    setResultFeedback((previous) => ({
      ...previous,
      [context.key]: {
        value: nextValue,
        isPending: true,
        error: false,
      },
    }));

    void submitFeedbackEvent({
      querySessionId: context.querySessionId,
      queryText: rawQuery,
      caseId: result.case_id,
      rank: context.rank,
      feedbackValue,
      searchMode: context.searchMode,
      confidenceLevel: context.confidenceLevel,
    }).then((feedbackResult) => {
      setResultFeedback((previous) => ({
        ...previous,
        [context.key]: {
          value: nextValue,
          isPending: false,
          error: !feedbackResult.sent,
        },
      }));
    });
  }

  function getFeedbackState(result: SearchResultItem) {
    const context = resolveFeedbackContext(result);
    return context ? resultFeedback[context.key] : undefined;
  }

  function resolveFeedbackContext(result: SearchResultItem): FeedbackContext | null {
    const primaryRank = resultResponse?.results.findIndex(
      (item) => item.case_id === result.case_id
    ) ?? -1;
    if (primaryRank >= 0 && resultResponse) {
      return makeFeedbackContext(result, {
        querySessionId: resultResponse.query_session_id,
        rank: primaryRank + 1,
        searchMode: normalizeSearchMode(resultResponse.coverage.search_mode),
        bucket: "primary",
      });
    }

    const lowConfidenceRank = resultResponse?.low_confidence_candidates.findIndex(
      (item) => item.case_id === result.case_id
    ) ?? -1;
    if (lowConfidenceRank >= 0 && resultResponse) {
      return makeFeedbackContext(result, {
        querySessionId: resultResponse.query_session_id,
        rank: lowConfidenceRank + 1,
        searchMode: normalizeSearchMode(resultResponse.coverage.search_mode),
        bucket: "low",
      });
    }

    const expandedPrimaryRank = expandResult?.response.results.findIndex(
      (item) => item.case_id === result.case_id
    ) ?? -1;
    if (expandedPrimaryRank >= 0 && expandResult) {
      return makeFeedbackContext(result, {
        querySessionId: expandResult.response.query_session_id,
        rank: expandedPrimaryRank + 1,
        searchMode: normalizeSearchMode(expandResult.response.coverage.search_mode),
        bucket: "expanded-primary",
      });
    }

    const expandedLowRank = expandResult?.response.low_confidence_candidates.findIndex(
      (item) => item.case_id === result.case_id
    ) ?? -1;
    if (expandedLowRank >= 0 && expandResult) {
      return makeFeedbackContext(result, {
        querySessionId: expandResult.response.query_session_id,
        rank: expandedLowRank + 1,
        searchMode: normalizeSearchMode(expandResult.response.coverage.search_mode),
        bucket: "expanded-low",
      });
    }

    return null;
  }

  function makeFeedbackContext(
    result: SearchResultItem,
    context: {
      querySessionId: string;
      rank: number;
      searchMode: FeedbackSearchMode;
      bucket: string;
    }
  ): FeedbackContext {
    return {
      key: `${context.querySessionId}:${context.searchMode}:${context.bucket}:${context.rank}:${result.case_id}`,
      querySessionId: context.querySessionId,
      rank: context.rank,
      searchMode: context.searchMode,
      confidenceLevel: normalizeConfidenceLevel(result.confidence_level ?? result.confidence),
    };
  }

  return (
    <main className="min-h-[100dvh] bg-[var(--color-bg)] text-[var(--color-text)]">
      <header className="sticky top-0 z-10 border-b border-[var(--color-border)] bg-[var(--color-bg)] px-4 py-3 sm:px-6">
        <div className="mx-auto flex w-full max-w-[1280px] flex-col gap-3">
          <div className="flex items-center justify-between gap-3">
            <Link
              to="/"
              className="text-sm font-medium text-[var(--color-brand)] hover:text-[var(--color-brand-hover)]"
            >
              返回首页
            </Link>
            <div className="flex items-center gap-2">
              {statuteSearchEnabled ? (
                <Link
                  to="/statute"
                  className="inline-flex rounded-[8px] border border-[var(--color-border-strong)] px-3 py-1.5 text-xs font-medium text-[var(--color-brand)] transition hover:bg-[var(--color-brand-soft)]"
                >
                  跳法条检索
                </Link>
              ) : null}
              {draftingEnabled ? (
                <Link
                  to="/drafting"
                  className="inline-flex rounded-[8px] border border-[var(--color-border-strong)] px-3 py-1.5 text-xs font-medium text-[var(--color-brand)] transition hover:bg-[var(--color-brand-soft)]"
                >
                  文书工作台
                </Link>
              ) : null}
              {casebookEnabled ? (
                <Link
                  to="/casebook"
                  className="inline-flex rounded-[8px] border border-[var(--color-border-strong)] px-3 py-1.5 text-xs font-medium text-[var(--color-brand)] transition hover:bg-[var(--color-brand-soft)]"
                >
                  协作工作台
                </Link>
              ) : null}
              <span className="hidden rounded-[4px] border border-[var(--color-border)] px-2.5 py-1 text-xs text-[var(--color-text-muted)] sm:inline-flex">
                覆盖信息以本次检索返回为准
              </span>
            </div>
          </div>

          <form
            className="grid gap-2 md:grid-cols-[1fr_auto]"
            onSubmit={(event) => {
              event.preventDefault();
              runSearch(query);
            }}
          >
            <div className="min-w-0">
              <label
                htmlFor="search-page-query"
                className="sr-only"
              >
                案情描述
              </label>
              <textarea
                ref={searchInputRef}
                id="search-page-query"
                value={query}
                disabled={isSearching}
                aria-invalid={Boolean(errorMessage)}
                aria-describedby={[
                  "search-page-query-hint",
                  errorMessage ? "search-page-query-error" : "",
                ]
                  .filter(Boolean)
                  .join(" ")}
                className="block min-h-[52px] max-h-[120px] w-full resize-y rounded-[8px] border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-base leading-6 text-[var(--color-text)] outline-none transition focus:border-[var(--color-brand)] focus:ring-2 focus:ring-[var(--color-brand-soft)] disabled:bg-[var(--color-surface-muted)]"
                placeholder="编辑案情描述后重新检索"
                onChange={(event) => {
                  setQuery(event.target.value);
                  setShowValidation(false);
                }}
                onKeyDown={(event) => {
                  if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
                    event.preventDefault();
                    runSearch(query, { trigger: "keyboard" });
                  }
                }}
              />
            </div>
            <div className="flex flex-row gap-2 md:flex-col">
              <button
                type="submit"
                disabled={!canSubmit}
                className="inline-flex h-[52px] min-w-[104px] flex-1 items-center justify-center rounded-[8px] bg-[var(--color-brand)] px-4 text-sm font-medium text-white transition hover:bg-[var(--color-brand-hover)] focus:outline-none focus:ring-2 focus:ring-[var(--color-brand-soft)] disabled:cursor-not-allowed disabled:bg-[var(--color-border-strong)] md:flex-none"
              >
                {isSearching ? "检索中..." : "重新检索"}
              </button>
              <button
                type="button"
                disabled={isSearching}
                className="inline-flex h-[52px] min-w-[104px] flex-1 items-center justify-center rounded-[8px] border border-[var(--color-border-strong)] bg-[var(--color-surface)] px-4 text-sm font-medium text-[var(--color-brand)] transition hover:bg-[var(--color-brand-soft)] focus:outline-none focus:ring-2 focus:ring-[var(--color-brand-soft)] disabled:cursor-not-allowed disabled:opacity-70 md:flex-none"
                onClick={() => runSearch(query, { useMock: true })}
              >
                测试数据
              </button>
            </div>
          </form>

          <div className="min-h-[20px] text-xs leading-5">
            <p id="search-page-query-hint" className="text-[var(--color-text-muted)]">
              {submittedQuery
                ? `当前检索输入 ${Array.from(submittedQuery).length} 字。`
                : typeof state?.inputLength === "number"
                  ? `来自首页输入 ${state.inputLength} 字。`
                  : "可从首页提交案情，或在此输入案情后检索。"}
            </p>
            {errorMessage ? (
              <p
                id="search-page-query-error"
                role="alert"
                className="text-[var(--color-danger)]"
              >
                {errorMessage}
              </p>
            ) : null}
          </div>
        </div>
      </header>

      <div className="mx-auto grid w-full max-w-[1280px] gap-4 px-4 py-5 sm:px-6 lg:grid-cols-[minmax(0,1fr)_280px]">
        <section className="min-w-0 space-y-4">
          {searchMutation.error ? (
            <ErrorBanner
              error={searchMutation.error}
              isRetrying={isSearching}
              onRetry={() => runSearch(submittedQuery || query, { trackSubmit: false })}
            />
          ) : null}

          {isSearching ? (
            <ResultList
              isLoading
              onEdit={() => searchInputRef.current?.focus()}
              onExpand={handleExpandSearch}
              onSelectResult={handleSelectResult}
            />
          ) : resultResponse && resultSource ? (
            <>
              <ResultOverview response={resultResponse} source={resultSource} />
              <RiskHintsPanel
                responses={
                  expandResult?.response
                    ? [resultResponse, expandResult.response]
                    : [resultResponse]
                }
                onSelectResult={handleSelectResult}
              />
              <ResultList
                response={resultResponse}
                expandResult={expandResult}
                isExpandLoading={expandMutation.isPending}
                expandError={expandMutation.error}
                expandedSearchEnabled={expandedSearchEnabled}
                isLoading={false}
                onEdit={() => searchInputRef.current?.focus()}
                onExpand={handleExpandSearch}
                onSelectResult={handleSelectResult}
                getFeedbackState={getFeedbackState}
                onFeedback={handleFeedback}
                getCompareSelection={getCompareSelection}
                getFavoriteSelection={getFavoriteSelection}
                getListSelection={getListSelection}
              />
            </>
          ) : (
            <section className="rounded-[8px] border border-[var(--color-border)] bg-[var(--color-surface)] p-5">
              <p className="text-sm font-medium text-[var(--color-brand)]">
                搜索结果页
              </p>
              <h1 className="mt-2 text-xl font-semibold">
                输入案情后查看相似案例
              </h1>
              <p className="mt-2 text-sm leading-6 text-[var(--color-text-muted)]">
                结果将展示数量、耗时、数据覆盖、降级状态、摘要、高亮和来源 chunk。若后端暂不可用，可用测试数据检查页面状态。
              </p>
            </section>
          )}
        </section>

        <aside className="space-y-3 lg:sticky lg:top-[152px] lg:self-start">
          {historyEnabled ? (
            <SearchHistoryPanel
              entries={historyEntries}
              draftRestored={draftRestored}
              hasDraft={hasDraft}
              onResearch={handleResearchFromHistory}
              onRemoveEntry={handleRemoveHistoryEntry}
              onClearHistory={handleClearHistory}
              onClearDraft={handleClearDraft}
            />
          ) : null}
          {favoriteEnabled ? (
            <FavoritesPanel
              favorites={favorites}
              onOpenDetail={handleOpenFavoriteDetail}
              onRemove={handleRemoveFavorite}
              onClearAll={handleClearFavorites}
              onUpdateFields={handleUpdateFavoriteFields}
            />
          ) : null}
          {listEnabled ? (
            <CaseListPanel
              lists={caseLists}
              onOpenDetail={handleOpenListItemDetail}
              exportEnabled={listExportEnabled}
              onExportList={handleExportList}
              reportEnabled={reportEnabled}
              onGenerateReport={handleGenerateReport}
              onDownloadReport={handleDownloadReport}
              onRemoveItem={handleRemoveListItem}
              onMoveItem={handleMoveListItem}
              onUpdateItemFields={handleUpdateListItemFields}
              onRenameList={handleRenameList}
              onDeleteList={handleDeleteList}
            />
          ) : null}
          <div className="rounded-[8px] border border-[var(--color-border)] bg-[var(--color-surface)] p-4 text-sm leading-6">
            <h2 className="font-semibold text-[var(--color-text)]">
              能力边界
            </h2>
            <p className="mt-2 text-[var(--color-text-muted)]">
              当前按事实相似度优先排序，不能证明相关案例完整范围，也不判断案件结果。降级时可能只使用基础检索策略。
            </p>
          </div>
          <div className="rounded-[8px] border border-[var(--color-border)] bg-[var(--color-surface)] p-4 text-xs leading-5 text-[var(--color-text-muted)]">
            <p className="font-medium text-[var(--color-text)]">隐私提示</p>
            <p className="mt-2">
              前端埋点只记录输入长度、结果数量和会话 ID，不发送原始案情文本；历史与草稿只保存在本浏览器、可清除，不上送后端。
            </p>
          </div>
        </aside>
      </div>

      {selectedDetailResult ? (
        <CaseDetailDrawer
          caseId={selectedDetailResult.result.case_id}
          seedResult={selectedDetailResult.result}
          querySessionId={selectedDetailResult.querySessionId}
          querySignal={submittedQuery || query}
          rank={selectedDetailResult.rank}
          useMock={resultSource === "mock"}
          onClose={handleCloseDetail}
          favoriteSelection={getDetailFavoriteSelection()}
        />
      ) : null}

      {compareSelectedResults.length > 0 && !isCompareOpen ? (
        <div className="fixed inset-x-0 bottom-0 z-20 border-t border-[var(--color-border)] bg-[var(--color-surface)] px-4 py-3 shadow-[0_-2px_8px_rgba(0,0,0,0.06)] sm:px-6">
          <div className="mx-auto flex w-full max-w-[1280px] flex-wrap items-center justify-between gap-3">
            <p className="text-sm text-[var(--color-text)]">
              已选择 {compareSelectedResults.length} 个案例用于本次对比
              <span className="ml-2 text-xs text-[var(--color-text-muted)]">
                （最多 {MAX_COMPARE_CASES} 个，仅本次阅读，不保存）
              </span>
            </p>
            <div className="flex items-center gap-2">
              <button
                type="button"
                className="inline-flex h-9 items-center justify-center rounded-[8px] border border-[var(--color-border-strong)] bg-[var(--color-surface)] px-3 text-sm font-medium text-[var(--color-text-muted)] transition hover:bg-[var(--color-surface-muted)] focus:outline-none focus:ring-2 focus:ring-[var(--color-brand-soft)]"
                onClick={() => setCompareCaseIds([])}
              >
                清空选择
              </button>
              <button
                type="button"
                disabled={compareSelectedResults.length < MIN_COMPARE_CASES}
                className="inline-flex h-9 items-center justify-center rounded-[8px] bg-[var(--color-brand)] px-4 text-sm font-medium text-white transition hover:bg-[var(--color-brand-hover)] focus:outline-none focus:ring-2 focus:ring-[var(--color-brand-soft)] disabled:cursor-not-allowed disabled:bg-[var(--color-border-strong)]"
                onClick={() => setIsCompareOpen(true)}
              >
                {compareSelectedResults.length < MIN_COMPARE_CASES
                  ? `再选 ${MIN_COMPARE_CASES - compareSelectedResults.length} 个开始对比`
                  : "打开对比视图"}
              </button>
            </div>
          </div>
        </div>
      ) : null}

      {isCompareOpen && compareSelectedResults.length >= MIN_COMPARE_CASES ? (
        <CaseCompareView
          selected={compareSelectedResults}
          riskHints={compareRiskHints}
          querySignal={submittedQuery || query}
          useMock={resultSource === "mock"}
          onClose={() => setIsCompareOpen(false)}
          getFavoriteSelection={getFavoriteSelection}
          getListSelection={getListSelection}
          onRemoveCase={(caseId) => {
            setCompareCaseIds((previous) => {
              const next = previous.filter((id) => id !== caseId);
              if (next.length < MIN_COMPARE_CASES) {
                setIsCompareOpen(false);
              }
              return next;
            });
          }}
        />
      ) : null}
    </main>
  );
}

function normalizeSearchMode(value: string | null | undefined): FeedbackSearchMode {
  return value === "expanded" ? "expanded" : "standard";
}

function normalizeConfidenceLevel(value: string | null | undefined): FeedbackConfidenceLevel {
  return value === "high" || value === "medium" || value === "low" ? value : "low";
}
