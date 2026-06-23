import { useEffect, useMemo, useRef, useState } from "react";
import { useQueries } from "@tanstack/react-query";

import {
  buildCaseCompare,
  COMPARE_DEGRADE_REASONS,
  summarizeCaseCompare,
  type CaseCompareSource,
  type CompareCell,
  type CompareDegradeReason,
  type CompareSection,
} from "../../lib/caseCompare";
import { CopyCitationButton } from "./CopyCitationButton";
import { FavoriteButton } from "./FavoriteButton";
import { AddToListButton, type ListSelectionState } from "./AddToListButton";
import { buildCitationFromResult } from "../../lib/citationCopy";
import { fetchCaseDetail, fetchFactAlignment } from "../../services/searchApi";
import type {
  CaseDetailResult,
  FactAlignmentResult,
  RiskHint,
  SearchResultItem,
  SourceAnchor,
} from "../../types/search";

// M4-3: 单案收藏切换状态（由父组件管理）。对比集合本身不被保存为收藏，
// 这里只针对对比视图内每个案例提供与结果卡片一致的收藏入口。
export type CompareFavoriteState = {
  favorited: boolean;
  onToggle: (result: SearchResultItem) => void;
};

type CaseCompareViewProps = {
  selected: SearchResultItem[];
  riskHints: RiskHint[];
  querySignal: string;
  useMock?: boolean;
  onClose: () => void;
  onRemoveCase: (caseId: string) => void;
  getFavoriteSelection?: (result: SearchResultItem) => CompareFavoriteState | undefined;
  getListSelection?: (result: SearchResultItem) => ListSelectionState | undefined;
};

const DEGRADE_LABELS: Record<CompareDegradeReason, string> = {
  detail_unavailable: "案例详情暂不可用",
  detail_loading: "加载中",
  module_degraded: "该模块本案降级，未展示",
  missing_source_anchor: "暂无可核验来源锚点",
  source_chunk_unavailable: "来源片段不可定位",
  no_anchored_content: "暂无可核验来源内容",
  no_flagged_risk: "未发现已标注风险线索",
};

export function CaseCompareView({
  selected,
  riskHints,
  querySignal,
  useMock,
  onClose,
  onRemoveCase,
  getFavoriteSelection,
  getListSelection,
}: CaseCompareViewProps) {
  const closeButtonRef = useRef<HTMLButtonElement | null>(null);
  const [activeMobileCaseId, setActiveMobileCaseId] = useState<string>(
    selected[0]?.case_id ?? ""
  );

  const caseIds = selected.map((item) => item.case_id);

  // Lazy detail fetch per selected case (reuses the case-detail endpoint).
  const detailQueries = useQueries({
    queries: caseIds.map((caseId) => ({
      queryKey: ["case-detail", caseId, useMock === true],
      queryFn: () => fetchCaseDetail(caseId, { useMock }),
      enabled: Boolean(caseId),
      retry: false,
    })),
  });

  // Lazy fact-alignment per case. Query signal stays in-request only and is
  // intentionally excluded from the cache key (mirrors useFactAlignment).
  const factQueries = useQueries({
    queries: caseIds.map((caseId) => ({
      queryKey: ["fact-alignment", caseId, useMock === true],
      queryFn: () => fetchFactAlignment(caseId, querySignal, { useMock }),
      enabled: Boolean(caseId),
      retry: false,
      staleTime: 0,
      gcTime: 0,
    })),
  });

  const compare = useMemo(() => {
    const sources: CaseCompareSource[] = selected.map((item, index) => {
      const detailQuery = detailQueries[index] as
        | { data?: CaseDetailResult; isLoading?: boolean }
        | undefined;
      const factQuery = factQueries[index] as
        | { data?: FactAlignmentResult; isLoading?: boolean }
        | undefined;
      return {
        caseId: item.case_id,
        seed: item,
        detail: detailQuery?.data?.response ?? null,
        detailLoading: Boolean(detailQuery?.isLoading),
        factAlignment: factQuery?.data?.response ?? null,
        factAlignmentLoading: Boolean(factQuery?.isLoading),
      };
    });
    return buildCaseCompare(sources, riskHints);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected, riskHints, ...detailQueries.map((q) => q.data), ...detailQueries.map((q) => q.isLoading), ...factQueries.map((q) => q.data), ...factQueries.map((q) => q.isLoading)]);

  // Sanitized, body-free trace for observability/gate evidence.
  useEffect(() => {
    if (typeof console === "undefined" || typeof console.info !== "function") {
      return;
    }
    try {
      console.info(
        JSON.stringify({
          event: "case_compare_render",
          ...summarizeCaseCompare(compare),
        })
      );
    } catch {
      // logging must never break the reading flow
    }
  }, [compare]);

  useEffect(() => {
    closeButtonRef.current?.focus({ preventScroll: true });
  }, []);

  useEffect(() => {
    function handleKey(event: KeyboardEvent) {
      if (event.key === "Escape") {
        onClose();
      }
    }
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, [onClose]);

  useEffect(() => {
    if (!caseIds.includes(activeMobileCaseId) && caseIds.length > 0) {
      setActiveMobileCaseId(caseIds[0]);
    }
  }, [caseIds, activeMobileCaseId]);

  const titleByCase = new Map(
    selected.map((item) => [item.case_id, item.title?.trim() || "未命名案例"])
  );

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="case-compare-heading"
      className="fixed inset-0 z-30 flex flex-col bg-black/40"
    >
      <button
        type="button"
        aria-label="关闭对比视图"
        className="absolute inset-0 h-full w-full cursor-default"
        tabIndex={-1}
        onClick={onClose}
      />
      <section className="relative mt-auto flex max-h-[92dvh] w-full flex-col rounded-t-[16px] border-t border-[var(--color-border)] bg-[var(--color-bg)] shadow-xl sm:mx-auto sm:mt-10 sm:max-h-[88dvh] sm:max-w-[1180px] sm:rounded-[16px]">
        <header className="flex items-start justify-between gap-3 border-b border-[var(--color-border)] px-4 py-3 sm:px-6">
          <div className="min-w-0">
            <p className="text-xs font-medium text-[var(--color-brand)]">本次阅读对比</p>
            <h2
              id="case-compare-heading"
              className="mt-1 text-lg font-semibold text-[var(--color-text)]"
            >
              案例横向对比（{selected.length}）
            </h2>
            <p className="mt-1 text-xs leading-5 text-[var(--color-text-muted)]">
              仅服务本次阅读判断，不保存为收藏 / 历史 / 清单 / 导出 / 报告，关闭后不影响主结果排序。
            </p>
          </div>
          <button
            ref={closeButtonRef}
            type="button"
            className="inline-flex h-9 shrink-0 items-center justify-center rounded-[8px] border border-[var(--color-border-strong)] bg-[var(--color-surface)] px-3 text-sm font-medium text-[var(--color-text)] transition hover:bg-[var(--color-surface-muted)] focus:outline-none focus:ring-2 focus:ring-[var(--color-brand-soft)]"
            onClick={onClose}
          >
            关闭对比
          </button>
        </header>

        <div className="flex flex-wrap gap-2 border-b border-[var(--color-border)] px-4 py-2 sm:px-6">
          {selected.map((item) => (
            <span
              key={item.case_id}
              className="inline-flex items-center gap-2 rounded-[6px] border border-[var(--color-border)] bg-[var(--color-surface)] px-2.5 py-1 text-xs text-[var(--color-text)]"
            >
              <span className="max-w-[200px] truncate">{titleByCase.get(item.case_id)}</span>
              <CopyCitationButton
                record={buildCitationFromResult(item)}
                kind="citation"
                surface="compare"
                label="复制引用"
                ariaLabel={`复制引用：${titleByCase.get(item.case_id)}`}
              />
              {getFavoriteSelection?.(item) ? (
                <FavoriteButton
                  favorited={getFavoriteSelection(item)!.favorited}
                  caseTitle={titleByCase.get(item.case_id) || "本案"}
                  onToggle={() => getFavoriteSelection(item)!.onToggle(item)}
                />
              ) : null}
              {getListSelection?.(item) ? (
                <AddToListButton
                  selection={getListSelection(item)!}
                  caseTitle={titleByCase.get(item.case_id) || "本案"}
                />
              ) : null}
              <button
                type="button"
                aria-label={`从对比中移除：${titleByCase.get(item.case_id)}`}
                className="text-[var(--color-text-muted)] hover:text-[var(--color-danger)]"
                onClick={() => onRemoveCase(item.case_id)}
              >
                ✕
              </button>
            </span>
          ))}
        </div>

        {/* Mobile: single-case segmented view */}
        <div className="border-b border-[var(--color-border)] px-4 py-2 sm:hidden">
          <label htmlFor="compare-mobile-case" className="sr-only">
            选择查看的案例
          </label>
          <select
            id="compare-mobile-case"
            value={activeMobileCaseId}
            className="block w-full rounded-[8px] border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-sm text-[var(--color-text)]"
            onChange={(event) => setActiveMobileCaseId(event.target.value)}
          >
            {selected.map((item) => (
              <option key={item.case_id} value={item.case_id}>
                {titleByCase.get(item.case_id)}
              </option>
            ))}
          </select>
        </div>

        <div className="min-h-0 flex-1 overflow-auto px-4 py-4 sm:px-6">
          {/* Desktop / tablet: matrix */}
          <div className="hidden sm:block">
            <table className="w-full border-collapse text-left text-sm">
              <thead>
                <tr>
                  <th className="sticky left-0 z-10 w-[160px] bg-[var(--color-bg)] py-2 pr-3 align-top text-xs font-medium text-[var(--color-text-muted)]">
                    对比维度
                  </th>
                  {selected.map((item) => (
                    <th
                      key={item.case_id}
                      className="min-w-[240px] py-2 pl-3 pr-3 align-top text-sm font-semibold text-[var(--color-text)]"
                    >
                      {titleByCase.get(item.case_id)}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {compare.compareSections.map((section) => (
                  <SectionRow key={section.key} section={section} caseIds={caseIds} />
                ))}
              </tbody>
            </table>
          </div>

          {/* Mobile: one case, sections stacked */}
          <div className="space-y-4 sm:hidden">
            {compare.compareSections.map((section) => {
              const cell = section.cells.find((c) => c.caseId === activeMobileCaseId);
              return (
                <div
                  key={section.key}
                  className="rounded-[8px] border border-[var(--color-border)] bg-[var(--color-surface)] p-3"
                >
                  <p className="text-xs font-medium text-[var(--color-text-muted)]">
                    {section.title}
                  </p>
                  <div className="mt-2">
                    {cell ? <CellBody cell={cell} /> : <DegradeNote reason="detail_unavailable" />}
                  </div>
                </div>
              );
            })}
          </div>
        </div>

        <footer className="border-t border-[var(--color-border)] px-4 py-2 text-[11px] leading-5 text-[var(--color-text-subtle)] sm:px-6">
          每个案例侧对比单元都附来源锚点，无可核验来源的内容不展示。对比不生成胜诉/败诉判断。
        </footer>
      </section>
    </div>
  );
}

function SectionRow({
  section,
  caseIds,
}: {
  section: CompareSection;
  caseIds: string[];
}) {
  const cellByCase = new Map(section.cells.map((cell) => [cell.caseId, cell]));
  return (
    <tr className="border-t border-[var(--color-border)] align-top">
      <th
        scope="row"
        className="sticky left-0 z-10 bg-[var(--color-bg)] py-3 pr-3 text-xs font-medium text-[var(--color-text-muted)]"
      >
        {section.title}
      </th>
      {caseIds.map((caseId) => {
        const cell = cellByCase.get(caseId);
        return (
          <td key={caseId} className="py-3 pl-3 pr-3">
            {cell ? <CellBody cell={cell} /> : <DegradeNote reason="detail_unavailable" />}
          </td>
        );
      })}
    </tr>
  );
}

function CellBody({ cell }: { cell: CompareCell }) {
  if (cell.status === "loading") {
    return <p className="text-xs text-[var(--color-text-muted)]">加载中……</p>;
  }
  if (cell.status === "degraded" || cell.entries.length === 0) {
    return <DegradeNote reason={cell.degradeReason} />;
  }
  return (
    <ul className="space-y-2">
      {cell.entries.map((entry, index) => (
        <li key={`${entry.anchor.source_chunk_id}-${index}`} className="text-sm leading-6">
          {entry.label ? (
            <span className="mr-1 rounded-[4px] bg-[var(--color-surface-muted)] px-1.5 py-0.5 text-[11px] text-[var(--color-text-muted)]">
              {entry.label}
            </span>
          ) : null}
          <span className="text-[var(--color-text)]">{entry.text}</span>
          <AnchorTag anchor={entry.anchor} />
        </li>
      ))}
    </ul>
  );
}

function AnchorTag({ anchor }: { anchor: SourceAnchor }) {
  return (
    <span
      className="mt-1 block break-all font-mono text-[10px] leading-4 text-[var(--color-text-subtle)]"
      title={`case_id: ${anchor.case_id}; source_chunk_id: ${anchor.source_chunk_id}`}
    >
      来源 {anchor.source_chunk_id}
    </span>
  );
}

function DegradeNote({ reason }: { reason?: CompareDegradeReason }) {
  const safeReason =
    reason && COMPARE_DEGRADE_REASONS.includes(reason) ? reason : "no_anchored_content";
  return (
    <p className="text-xs leading-5 text-[var(--color-text-muted)]">
      {DEGRADE_LABELS[safeReason]}
      <span className="ml-1 font-mono text-[10px] text-[var(--color-text-subtle)]">
        （{safeReason}）
      </span>
    </p>
  );
}
