import { useEffect, useMemo, useRef, useState } from "react";

import { useAnalytics } from "../../hooks/useAnalytics";
import { useCaseDetail } from "../../hooks/useCaseDetail";
import { useFactAlignment } from "../../hooks/useFactAlignment";
import { CopyCitationButton } from "../results/CopyCitationButton";
import { FavoriteButton } from "../results/FavoriteButton";
import { buildCitationFromDetail } from "../../lib/citationCopy";
import {
  logHighlightNavigation,
  navigateToSourceChunk,
  sourceChunkElementId,
  type HighlightRelatedModule,
} from "../../lib/sourceHighlights";
import type {
  CaseChunk,
  CaseDetailResponse,
  FactAlignmentItem,
  HoldingSummaryItem,
  ReadingNavigationItem,
  ReadingNavigationSection as ReadingNavigationSectionType,
  SearchResultItem,
  SourceAnchor as SourceAnchorType,
} from "../../types/search";

type CaseDetailFavoriteState = {
  favorited: boolean;
  onToggle: (detail: CaseDetailResponse, seed?: SearchResultItem) => void;
};

type CaseDetailDrawerProps = {
  caseId: string;
  seedResult?: SearchResultItem;
  querySessionId?: string | null;
  querySignal?: string;
  rank: number;
  useMock?: boolean;
  onClose: () => void;
  favoriteSelection?: CaseDetailFavoriteState;
};

type VerifiedHoldingSummaryItem = {
  text: string;
  sourceAnchors: SourceAnchorType[];
  confidence?: string;
};

type VerifiedReadingNavigationItem = {
  label: string;
  category: string;
  sourceAnchors: SourceAnchorType[];
  confidence?: string;
};

type VerifiedFactAlignmentItem = {
  dimension: string;
  dimensionKey: string;
  querySideSignal: string;
  caseSideFacts: string[];
  sourceAnchors: SourceAnchorType[];
  matchType: string;
  confidence?: string;
};

const allowedFactMatchTypes = new Set([
  "same_dimension",
  "similar_dimension",
  "difference_to_review",
]);

const allowedReadingCategories = new Set([
  "争议焦点",
  "裁判理由中的关键事实",
  "法院认定的关键要素",
  "与用户阅读相关的程序或证据节点",
]);

const forbiddenReadingTerms = [
  "胜诉",
  "败诉",
  "概率",
  "诉讼结果",
  "确定性法律结论",
  "风险评级",
  "本案应当如何判",
  "已查全",
  "保证无遗漏",
  "必然支持",
];

const focusableSelector = [
  "a[href]",
  "button:not([disabled])",
  "textarea:not([disabled])",
  "input:not([disabled])",
  "select:not([disabled])",
  "[tabindex]:not([tabindex='-1'])",
].join(",");

export function CaseDetailDrawer({
  caseId,
  seedResult,
  querySessionId,
  querySignal,
  rank,
  useMock,
  onClose,
  favoriteSelection,
}: CaseDetailDrawerProps) {
  const analytics = useAnalytics();
  const drawerRef = useRef<HTMLElement | null>(null);
  const closeButtonRef = useRef<HTMLButtonElement | null>(null);
  const detailViewTrackedRef = useRef(false);
  const detailQuery = useCaseDetail(caseId, { useMock });
  const detail = detailQuery.data?.response;
  const source = detailQuery.data?.source;

  useEffect(() => {
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    window.setTimeout(() => closeButtonRef.current?.focus(), 0);

    return () => {
      document.body.style.overflow = previousOverflow;
    };
  }, []);

  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
        return;
      }

      if (event.key !== "Tab" || !drawerRef.current) {
        return;
      }

      const focusable = Array.from(
        drawerRef.current.querySelectorAll<HTMLElement>(focusableSelector)
      ).filter((item) => !item.hasAttribute("disabled"));

      if (focusable.length === 0) {
        event.preventDefault();
        drawerRef.current.focus();
        return;
      }

      const first = focusable[0];
      const last = focusable[focusable.length - 1];

      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }

    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [onClose]);

  useEffect(() => {
    if (!detail || detailViewTrackedRef.current) {
      return;
    }

    detailViewTrackedRef.current = true;
    void analytics.trackCaseDetailView({
      query_session_id: querySessionId,
      case_id: caseId,
      rank,
    });
  }, [analytics, caseId, detail, querySessionId, rank]);

  return (
    <div
      className="fixed inset-0 z-50 flex justify-end overflow-hidden bg-slate-950/24"
      aria-labelledby="case-detail-title"
      role="presentation"
    >
      <div className="hidden flex-1 lg:block" onClick={onClose} />
      <section
        ref={drawerRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="case-detail-title"
        tabIndex={-1}
        className="flex h-[100dvh] w-full max-w-none flex-col overflow-hidden border-l border-[var(--color-border)] bg-[var(--color-surface)] shadow-2xl outline-none lg:w-[min(560px,42vw)] lg:min-w-[440px]"
      >
        <header className="sticky top-0 z-10 border-b border-[var(--color-border)] bg-[var(--color-surface)] px-4 py-3 sm:px-5">
          <div className="relative min-h-10">
            <button
              ref={closeButtonRef}
              type="button"
              aria-label="关闭案例详情抽屉"
              title="关闭案例详情"
              className="mb-3 inline-flex h-10 shrink-0 items-center justify-center rounded-[8px] border border-[var(--color-border-strong)] bg-[var(--color-surface)] px-3 text-sm font-medium text-[var(--color-text)] transition hover:bg-[var(--color-surface-muted)] focus:outline-none focus:ring-2 focus:ring-[var(--color-brand-soft)] sm:absolute sm:right-0 sm:top-0 sm:mb-0"
              onClick={onClose}
            >
              关闭
            </button>
            <div className="min-w-0 sm:pr-20">
              <p className="text-xs font-medium text-[var(--color-brand)]">
                案例详情
              </p>
              <h2
                id="case-detail-title"
                className="mt-1 break-words text-base font-semibold leading-6 text-[var(--color-text)]"
              >
                {detail?.title?.trim() || seedResult?.title?.trim() || "未命名案例"}
              </h2>
              <p className="mt-1 break-words text-xs leading-5 text-[var(--color-text-muted)]">
                {detail?.case_no || seedResult?.case_no || "案号暂缺"}
              </p>
            </div>
          </div>
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4 sm:px-5">
          {detailQuery.isPending ? <CaseDetailSkeleton /> : null}

          {detailQuery.isError ? (
            <CaseDetailError
              error={detailQuery.error}
              isRetrying={detailQuery.isFetching}
              onRetry={() => void detailQuery.refetch()}
            />
          ) : null}

          {detail && !detailQuery.isError ? (
            <CaseDetailContent
              detail={detail}
              seedResult={seedResult}
              source={source}
              querySignal={querySignal}
              useMock={useMock}
              favoriteSelection={favoriteSelection}
            />
          ) : null}
        </div>
      </section>
    </div>
  );
}

function CaseDetailSkeleton() {
  return (
    <div aria-label="案例详情加载中" className="animate-pulse space-y-5">
      {[96, 128, 168, 180].map((height, index) => (
        <div
          key={height}
          className="rounded-[8px] border border-[var(--color-border)] p-4"
        >
          <div className="h-4 w-28 rounded bg-[var(--color-surface-muted)]" />
          <div
            className="mt-4 rounded bg-[var(--color-surface-muted)]"
            style={{ height }}
            aria-hidden="true"
          />
          {index === 0 ? (
            <div className="mt-3 h-4 w-3/4 rounded bg-[var(--color-surface-muted)]" />
          ) : null}
        </div>
      ))}
    </div>
  );
}

function CaseDetailError({
  error,
  isRetrying,
  onRetry,
}: {
  error: Error;
  isRetrying: boolean;
  onRetry: () => void;
}) {
  return (
    <div
      role="alert"
      className="rounded-[8px] border border-[var(--color-danger)] bg-[var(--color-danger-soft)] p-4"
    >
      <p className="text-sm font-semibold text-[var(--color-danger)]">
        案例详情加载失败
      </p>
      <p className="mt-2 text-sm leading-6 text-[var(--color-text)]">
        {error.message || "暂时无法读取该案例详情，请稍后重试。"}
      </p>
      <button
        type="button"
        disabled={isRetrying}
        className="mt-3 inline-flex h-10 items-center justify-center rounded-[8px] bg-[var(--color-brand)] px-4 text-sm font-medium text-white transition hover:bg-[var(--color-brand-hover)] focus:outline-none focus:ring-2 focus:ring-[var(--color-brand-soft)] disabled:cursor-not-allowed disabled:opacity-70"
        onClick={onRetry}
      >
        {isRetrying ? "重试中..." : "重试"}
      </button>
    </div>
  );
}

function CaseDetailContent({
  detail,
  seedResult,
  source,
  querySignal,
  useMock,
  favoriteSelection,
}: {
  detail: CaseDetailResponse;
  seedResult?: SearchResultItem;
  source?: "api" | "mock";
  querySignal?: string;
  useMock?: boolean;
  favoriteSelection?: CaseDetailFavoriteState;
}) {
  const chunks = useMemo(
    () =>
      detail.chunks.filter(
        (chunk) => chunk.chunk_id && chunk.text?.trim() && hasChunkAnchor(chunk)
      ),
    [detail.chunks]
  );
  const factChunks = chunks.filter((chunk) =>
    ["fact", "court_found"].includes(chunk.chunk_type || "")
  );
  const holdingChunks = chunks.filter((chunk) =>
    ["court_opinion", "judgment_result"].includes(chunk.chunk_type || "")
  );
  const summary = getVerifiedSummary({
    detail,
    seedResult,
    factChunks,
  });
  const holdingSummaryItems = getVerifiedHoldingSummaryItems(detail);
  const holdingSummarySourceChunkIds = holdingSummaryItems.flatMap((item) =>
    item.sourceAnchors.map((anchor) => anchor.source_chunk_id)
  );
  const issueFocusItems = getVerifiedReadingNavigationItems({
    detail,
    section: detail.issue_focus,
    categories: ["争议焦点"],
  });
  const keyElementItems = getVerifiedReadingNavigationItems({
    detail,
    section: detail.key_elements,
    categories: [
      "裁判理由中的关键事实",
      "法院认定的关键要素",
      "与用户阅读相关的程序或证据节点",
    ],
  });
  const readingNavigationSourceChunkIds = [
    ...issueFocusItems,
    ...keyElementItems,
  ].flatMap((item) => item.sourceAnchors.map((anchor) => anchor.source_chunk_id));
  const sourceUrl = detail.source_url?.trim() || seedResult?.source_url?.trim() || "";
  const sourceChunks = pickSourceChunks({
    chunks,
    seedSourceChunkIds: [
      ...(seedResult?.source_chunk_ids || []),
      ...holdingSummarySourceChunkIds,
      ...readingNavigationSourceChunkIds,
    ],
  });

  function openSourceChunk(
    chunkId: string,
    relatedModule: HighlightRelatedModule = "holding_summary",
    anchorType?: string
  ) {
    const result = navigateToSourceChunk({ chunkId, anchorType });
    logHighlightNavigation({ relatedModule, result });
    return result;
  }

  const openHoldingSource = (chunkId: string) =>
    openSourceChunk(chunkId, "holding_summary");
  const openIssueFocusSource = (chunkId: string) =>
    openSourceChunk(chunkId, "issue_focus");
  const openKeyElementsSource = (chunkId: string) =>
    openSourceChunk(chunkId, "key_elements");
  const openFactAlignmentSource = (chunkId: string) =>
    openSourceChunk(chunkId, "fact_alignment");

  return (
    <div className="space-y-5">
      {source === "mock" ? (
        <div className="rounded-[8px] border border-[var(--color-warning)] bg-[var(--color-warning-soft)] px-3 py-2 text-xs leading-5 text-[var(--color-text)]">
          当前使用前端测试数据，案例详情为非真实样例，仅用于验证抽屉渲染。
        </div>
      ) : null}

      <section aria-labelledby="case-detail-meta-heading">
        <h3
          id="case-detail-meta-heading"
          className="text-sm font-semibold text-[var(--color-text)]"
        >
          元数据
        </h3>
        <dl className="mt-3 grid grid-cols-1 gap-3 text-sm sm:grid-cols-2">
          <MetaItem label="法院" value={detail.court || seedResult?.court} />
          <MetaItem
            label="审级"
            value={
              detail.trial_level ||
              detail.court_level ||
              seedResult?.trial_level ||
              seedResult?.court_level
            }
          />
          <MetaItem label="案由" value={detail.case_cause || seedResult?.case_cause} />
          <MetaItem
            label="日期"
            value={detail.judgment_date || seedResult?.judgment_date}
          />
        </dl>
        <div className="mt-3 flex flex-wrap gap-2">
          <CopyCitationButton
            record={buildCitationFromDetail(detail, seedResult)}
            kind="citation"
            surface="detail"
            label="复制引用格式"
            ariaLabel="复制本案基础引用格式"
            size="md"
          />
          <CopyCitationButton
            record={buildCitationFromDetail(detail, seedResult)}
            kind="case_number"
            surface="detail"
            label="复制案号"
            ariaLabel="复制本案案号"
            size="md"
          />
          {favoriteSelection ? (
            <FavoriteButton
              favorited={favoriteSelection.favorited}
              caseTitle={detail.title?.trim() || seedResult?.title?.trim() || "本案"}
              size="md"
              onToggle={() => favoriteSelection.onToggle(detail, seedResult)}
            />
          ) : null}
        </div>
        <p className="mt-2 text-[11px] leading-4 text-[var(--color-text-subtle)]">
          仅复制案号与基础引用格式（法院 / 案号 / 审级 / 裁判日期），不含摘要、要旨、片段或裁判正文。
        </p>
      </section>

      <Divider />

      <section aria-labelledby="case-detail-summary-heading">
        <SectionHeading
          id="case-detail-summary-heading"
          title="完整摘要"
          note="仅展示有来源片段支撑的内容"
        />
        {summary ? (
          <div className="mt-3 space-y-2">
            <SourceAnchor anchor={summary.anchor} />
            <p className="whitespace-pre-wrap text-sm leading-7 text-[var(--color-text)]">
              {summary.text}
            </p>
          </div>
        ) : (
          <FallbackNotice text="暂无可核验摘要来源，未展示 AI 摘要。" />
        )}
      </section>

      <Divider />

      <section aria-labelledby="case-detail-holding-heading">
        <SectionHeading
          id="case-detail-holding-heading"
          title="裁判要旨摘要"
          note="阅读辅助内容，仅用于定位裁判说理来源"
        />
        {holdingSummaryItems.length > 0 ? (
          <div className="mt-3 space-y-3">
            {holdingSummaryItems.map((item, index) => (
              <HoldingSummaryItemCard
                key={`${item.sourceAnchors[0]?.source_chunk_id || "holding"}-${index}`}
                item={item}
                onOpenSource={openHoldingSource}
              />
            ))}
          </div>
        ) : (
          <div>
            <FallbackNotice
              text={formatHoldingDegradeReason(detail.holding_summary?.degrade_reason)}
            />
            {holdingChunks.length > 0 ? (
              <SourceEntryList
                chunks={holdingChunks.slice(0, 3)}
                onOpenSource={openHoldingSource}
              />
            ) : null}
          </div>
        )}
      </section>

      <Divider />

      <section aria-labelledby="case-detail-reading-navigation-heading">
        <SectionHeading
          id="case-detail-reading-navigation-heading"
          title="争议焦点与关键要素"
          note="复核线索与阅读定位，均需回到来源片段确认"
        />
        {issueFocusItems.length > 0 || keyElementItems.length > 0 ? (
          <div className="mt-3 space-y-3">
            {issueFocusItems.length > 0 ? (
              <ReadingNavigationGroup
                title="争议焦点"
                items={issueFocusItems}
                onOpenSource={openIssueFocusSource}
              />
            ) : null}
            {keyElementItems.length > 0 ? (
              <ReadingNavigationGroup
                title="关键要素"
                items={keyElementItems}
                onOpenSource={openKeyElementsSource}
              />
            ) : null}
          </div>
        ) : (
          <div>
            <FallbackNotice
              text={formatReadingNavigationDegradeReason(
                detail.issue_focus?.degrade_reason ||
                  detail.key_elements?.degrade_reason
              )}
            />
            {chunks.length > 0 ? (
              <SourceEntryList
                chunks={[...factChunks, ...holdingChunks].slice(0, 3)}
                onOpenSource={openIssueFocusSource}
              />
            ) : null}
          </div>
        )}
      </section>

      <Divider />

      <FactAlignmentSection
        caseId={detail.case_id}
        detail={detail}
        querySignal={querySignal}
        useMock={useMock}
        onOpenSource={openFactAlignmentSource}
      />

      <Divider />

      <section aria-labelledby="case-detail-source-heading">
        <SectionHeading
          id="case-detail-source-heading"
          title="来源片段"
          note="每段均标明 source_chunk_id"
        />
        {sourceChunks.length > 0 ? (
          <div className="mt-3 space-y-3">
            {sourceChunks.map((chunk) => (
              <SourceExcerpt key={chunk.chunk_id} chunk={chunk} />
            ))}
          </div>
        ) : (
          <FallbackNotice text="当前详情未返回可核验来源片段。" />
        )}
      </section>

      <Divider />

      <section aria-labelledby="case-detail-link-heading" className="pb-2">
        <h3
          id="case-detail-link-heading"
          className="text-sm font-semibold text-[var(--color-text)]"
        >
          原文链接
        </h3>
        {isUsableUrl(sourceUrl) ? (
          <a
            className="mt-3 inline-flex max-w-full items-center justify-center rounded-[8px] border border-[var(--color-border-strong)] bg-[var(--color-surface)] px-3 py-2 text-sm font-medium text-[var(--color-brand)] transition hover:bg-[var(--color-brand-soft)] focus:outline-none focus:ring-2 focus:ring-[var(--color-brand-soft)]"
            href={sourceUrl}
            target="_blank"
            rel="noreferrer"
          >
            打开原文
          </a>
        ) : (
          <FallbackNotice
            text={
              detail.source_name
                ? `原文链接暂不可达或未提供，来源标识：${detail.source_name}。`
                : "原文链接暂不可达或未提供，请以来源片段复核。"
            }
          />
        )}
      </section>
    </div>
  );
}

function FactAlignmentSection({
  caseId,
  detail,
  querySignal,
  useMock,
  onOpenSource,
}: {
  caseId: string;
  detail: CaseDetailResponse;
  querySignal?: string;
  useMock?: boolean;
  onOpenSource: (chunkId: string) => void;
}) {
  const [requested, setRequested] = useState(false);
  const trimmedSignal = (querySignal || "").trim();
  const factAlignmentQuery = useFactAlignment(caseId, trimmedSignal, {
    useMock,
    enabled: requested,
  });
  const alignment = factAlignmentQuery.data?.response;
  const items = alignment
    ? getVerifiedFactAlignmentItems({ detail, alignment })
    : [];

  return (
    <section aria-labelledby="case-detail-fact-alignment-heading">
      <SectionHeading
        id="case-detail-fact-alignment-heading"
        title="相似事实对比"
        note="对照阅读线索：相同 / 相近 / 需复核差异，均需回到来源片段确认，不代表是否适用本案"
      />

      {!requested ? (
        <div className="mt-3">
          <button
            type="button"
            className="inline-flex h-10 items-center justify-center rounded-[8px] border border-[var(--color-border-strong)] bg-[var(--color-surface)] px-4 text-sm font-medium text-[var(--color-brand)] transition hover:bg-[var(--color-brand-soft)] focus:outline-none focus:ring-2 focus:ring-[var(--color-brand-soft)]"
            onClick={() => setRequested(true)}
          >
            加载事实对比
          </button>
          <p className="mt-2 text-xs leading-5 text-[var(--color-text-muted)]">
            按需加载。系统仅在本次请求内临时抽象你的输入用于对照，不保存原始案情。
          </p>
        </div>
      ) : null}

      {requested && factAlignmentQuery.isPending ? (
        <p className="mt-3 rounded-[8px] border border-[var(--color-border)] bg-[var(--color-surface-muted)] px-3 py-2 text-sm leading-6 text-[var(--color-text-muted)]">
          事实对比加载中……
        </p>
      ) : null}

      {requested && factAlignmentQuery.isError ? (
        <div className="mt-3">
          <FallbackNotice text="事实对比暂时不可用，详情其他内容不受影响。可稍后重试。" />
          <button
            type="button"
            disabled={factAlignmentQuery.isFetching}
            className="mt-3 inline-flex h-9 items-center justify-center rounded-[8px] border border-[var(--color-border-strong)] bg-[var(--color-surface)] px-3 text-xs font-medium text-[var(--color-brand)] transition hover:bg-[var(--color-brand-soft)] focus:outline-none focus:ring-2 focus:ring-[var(--color-brand-soft)] disabled:cursor-not-allowed disabled:opacity-70"
            onClick={() => void factAlignmentQuery.refetch()}
          >
            {factAlignmentQuery.isFetching ? "重试中..." : "重试"}
          </button>
        </div>
      ) : null}

      {requested && alignment && !factAlignmentQuery.isError ? (
        items.length > 0 ? (
          <div className="mt-3 space-y-3">
            {!alignment.query_signal_present ? (
              <p className="rounded-[8px] border border-[var(--color-border)] bg-[var(--color-surface-muted)] px-3 py-2 text-xs leading-5 text-[var(--color-text-muted)]">
                当前未识别到你输入中的对应事实维度，以下仅列出案例侧事实维度供复核差异。
              </p>
            ) : null}
            {items.map((item, index) => (
              <FactAlignmentItemCard
                key={`${item.dimensionKey}-${index}`}
                item={item}
                onOpenSource={onOpenSource}
              />
            ))}
          </div>
        ) : (
          <FallbackNotice
            text={formatFactAlignmentDegradeReason(alignment.degrade_reason)}
          />
        )
      ) : null}
    </section>
  );
}

function FactAlignmentItemCard({
  item,
  onOpenSource,
}: {
  item: VerifiedFactAlignmentItem;
  onOpenSource: (chunkId: string) => void;
}) {
  const primaryAnchor = item.sourceAnchors[0];

  return (
    <article className="rounded-[8px] border border-[var(--color-border)] bg-[var(--color-surface)] p-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="rounded-[4px] border border-[var(--color-border)] px-2 py-1 text-[11px] text-[var(--color-text-muted)]">
          维度：{item.dimension}
        </span>
        <span className={factMatchBadgeClass(item.matchType)}>
          {formatFactMatchType(item.matchType)}
        </span>
        {item.confidence ? (
          <span className="rounded-[4px] border border-[var(--color-border)] px-2 py-1 text-[11px] text-[var(--color-text-muted)]">
            置信度：{formatConfidence(item.confidence)}
          </span>
        ) : null}
      </div>
      <dl className="mt-2 space-y-1 text-sm leading-6 text-[var(--color-text)]">
        <div className="flex gap-2">
          <dt className="shrink-0 text-xs font-medium text-[var(--color-text-muted)]">
            本次输入
          </dt>
          <dd className="break-words">{formatFactQuerySignal(item.querySideSignal)}</dd>
        </div>
        <div className="flex gap-2">
          <dt className="shrink-0 text-xs font-medium text-[var(--color-text-muted)]">
            案例事实
          </dt>
          <dd className="break-words">{item.caseSideFacts.join("；")}</dd>
        </div>
      </dl>
      <div className="mt-2 flex flex-wrap items-center gap-2">
        {item.sourceAnchors.map((anchor) => (
          <SourceAnchor
            key={`${anchor.case_id}-${anchor.source_chunk_id}`}
            anchor={anchor}
            onOpenSource={onOpenSource}
          />
        ))}
        {primaryAnchor ? (
          <button
            type="button"
            className="inline-flex h-8 items-center justify-center rounded-[8px] border border-[var(--color-border-strong)] bg-[var(--color-surface)] px-3 text-xs font-medium text-[var(--color-brand)] transition hover:bg-[var(--color-brand-soft)] focus:outline-none focus:ring-2 focus:ring-[var(--color-brand-soft)]"
            onClick={() => onOpenSource(primaryAnchor.source_chunk_id)}
          >
            查看来源片段
          </button>
        ) : null}
      </div>
    </article>
  );
}

function getVerifiedFactAlignmentItems({
  detail,
  alignment,
}: {
  detail: CaseDetailResponse;
  alignment: { items?: FactAlignmentItem[]; generation_status?: string };
}): VerifiedFactAlignmentItem[] {
  if (!alignment || alignment.generation_status !== "generated") {
    return [];
  }

  return (alignment.items || [])
    .map((item) => getVerifiedFactAlignmentItem({ detail, item }))
    .filter((item): item is VerifiedFactAlignmentItem => Boolean(item));
}

function getVerifiedFactAlignmentItem({
  detail,
  item,
}: {
  detail: CaseDetailResponse;
  item: FactAlignmentItem;
}): VerifiedFactAlignmentItem | null {
  const dimension = item.dimension?.trim();
  const matchType = item.match_type?.trim();
  if (!dimension || !matchType || !allowedFactMatchTypes.has(matchType)) {
    return null;
  }

  const caseSideFacts = (item.case_side_facts || [])
    .map((fact) => fact?.trim())
    .filter((fact): fact is string => Boolean(fact))
    .filter((fact) => !forbiddenReadingTerms.some((term) => fact.includes(term)));
  if (caseSideFacts.length === 0) {
    return null;
  }

  // Case-side facts must be anchored to this case's real chunks.
  const sourceAnchors = (item.source_anchors || []).filter(
    (anchor) =>
      isSourceAnchor(anchor) &&
      anchor.case_id === detail.case_id &&
      detail.chunks.some(
        (chunk) =>
          chunk.chunk_id === anchor.source_chunk_id && hasChunkAnchor(chunk)
      )
  );
  if (sourceAnchors.length === 0) {
    return null;
  }

  return {
    dimension,
    dimensionKey: item.dimension_key?.trim() || dimension,
    querySideSignal: item.query_side_signal?.trim() || "",
    caseSideFacts,
    sourceAnchors,
    matchType,
    confidence: item.confidence,
  };
}

function formatFactMatchType(matchType: string) {
  const labels: Record<string, string> = {
    same_dimension: "相同维度",
    similar_dimension: "相近维度",
    difference_to_review: "需复核差异",
  };
  return labels[matchType] || "需复核差异";
}

function factMatchBadgeClass(matchType: string) {
  const base =
    "rounded-[4px] px-2 py-1 text-[11px] border";
  if (matchType === "same_dimension") {
    return `${base} border-[var(--color-success,#3f7d58)] text-[var(--color-text)]`;
  }
  if (matchType === "similar_dimension") {
    return `${base} border-[var(--color-border-strong)] text-[var(--color-text)]`;
  }
  return `${base} border-[var(--color-warning)] text-[var(--color-text)]`;
}

function formatFactQuerySignal(signal: string) {
  const labels: Record<string, string> = {
    input_signals_dimension: "输入包含该事实维度",
    input_does_not_mention_dimension: "输入未明确提及该维度",
  };
  return labels[signal] || "输入未明确提及该维度";
}

function formatFactAlignmentDegradeReason(reason?: string | null) {
  const labels: Record<string, string> = {
    missing_source_anchor: "案例侧事实缺少可核验来源锚点，未展示事实对比。",
    insufficient_source: "暂无足够可核验来源支撑事实对比，已保留来源片段入口供复核。",
    missing_query_signal: "未识别到可对照的输入事实维度，可回到来源片段自行复核。",
    fact_alignment_timeout: "事实对比生成超时，详情其他内容不受影响，可稍后重试。",
    fact_alignment_failed: "事实对比暂不可用，详情其他内容不受影响，可稍后重试。",
  };
  return labels[reason || ""] || "暂无可核验事实对比，已保留来源片段入口供复核。";
}

function MetaItem({ label, value }: { label: string; value?: string | null }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs font-medium text-[var(--color-text-muted)]">{label}</dt>
      <dd className="mt-1 break-words text-sm leading-6 text-[var(--color-text)]">
        {value?.trim() || "暂缺"}
      </dd>
    </div>
  );
}

function SectionHeading({
  id,
  title,
  note,
}: {
  id: string;
  title: string;
  note: string;
}) {
  return (
    <div>
      <h3 id={id} className="text-sm font-semibold text-[var(--color-text)]">
        {title}
      </h3>
      <p className="mt-1 text-xs leading-5 text-[var(--color-text-muted)]">{note}</p>
    </div>
  );
}

function SourceExcerpt({ chunk }: { chunk: CaseChunk }) {
  const anchor = getChunkAnchor(chunk);
  if (!anchor) {
    return null;
  }

  return (
    <article
      id={sourceChunkElementId(chunk.chunk_id)}
      className="scroll-mt-4 border-l-2 border-[var(--color-highlight)] pl-3"
    >
      <div className="flex flex-wrap items-center gap-2">
        <SourceAnchor anchor={anchor} />
        {chunk.chunk_type ? (
          <span className="rounded-[4px] border border-[var(--color-border)] px-2 py-1 text-[11px] text-[var(--color-text-muted)]">
            {formatChunkType(chunk.chunk_type)}
          </span>
        ) : null}
      </div>
      <p className="mt-2 whitespace-pre-wrap break-words text-sm leading-7 text-[var(--color-text)]">
        {chunk.text}
      </p>
    </article>
  );
}

function HoldingSummaryItemCard({
  item,
  onOpenSource,
}: {
  item: VerifiedHoldingSummaryItem;
  onOpenSource: (chunkId: string) => void;
}) {
  const primaryAnchor = item.sourceAnchors[0];

  return (
    <article className="rounded-[8px] border border-[var(--color-border)] bg-[var(--color-surface)] p-3">
      <div className="flex flex-wrap items-center gap-2">
        {item.sourceAnchors.map((anchor) => (
          <SourceAnchor
            key={`${anchor.case_id}-${anchor.source_chunk_id}`}
            anchor={anchor}
            onOpenSource={onOpenSource}
          />
        ))}
        {item.confidence ? (
          <span className="rounded-[4px] border border-[var(--color-border)] px-2 py-1 text-[11px] text-[var(--color-text-muted)]">
            置信度：{formatConfidence(item.confidence)}
          </span>
        ) : null}
      </div>
      <p className="mt-2 whitespace-pre-wrap break-words text-sm leading-7 text-[var(--color-text)]">
        {item.text}
      </p>
      {primaryAnchor ? (
        <button
          type="button"
          className="mt-2 inline-flex h-8 items-center justify-center rounded-[8px] border border-[var(--color-border-strong)] bg-[var(--color-surface)] px-3 text-xs font-medium text-[var(--color-brand)] transition hover:bg-[var(--color-brand-soft)] focus:outline-none focus:ring-2 focus:ring-[var(--color-brand-soft)]"
          onClick={() => onOpenSource(primaryAnchor.source_chunk_id)}
        >
          查看来源片段
        </button>
      ) : null}
    </article>
  );
}

function ReadingNavigationGroup({
  title,
  items,
  onOpenSource,
}: {
  title: string;
  items: VerifiedReadingNavigationItem[];
  onOpenSource: (chunkId: string) => void;
}) {
  return (
    <div>
      <h4 className="text-xs font-semibold text-[var(--color-text-muted)]">
        {title}
      </h4>
      <div className="mt-2 space-y-2">
        {items.map((item, index) => (
          <ReadingNavigationItemCard
            key={`${item.category}-${item.sourceAnchors[0]?.source_chunk_id || index}`}
            item={item}
            onOpenSource={onOpenSource}
          />
        ))}
      </div>
    </div>
  );
}

function ReadingNavigationItemCard({
  item,
  onOpenSource,
}: {
  item: VerifiedReadingNavigationItem;
  onOpenSource: (chunkId: string) => void;
}) {
  const primaryAnchor = item.sourceAnchors[0];

  return (
    <article className="rounded-[8px] border border-[var(--color-border)] bg-[var(--color-surface)] p-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="rounded-[4px] border border-[var(--color-border)] px-2 py-1 text-[11px] text-[var(--color-text-muted)]">
          {item.category}
        </span>
        {item.confidence ? (
          <span className="rounded-[4px] border border-[var(--color-border)] px-2 py-1 text-[11px] text-[var(--color-text-muted)]">
            置信度：{formatConfidence(item.confidence)}
          </span>
        ) : null}
      </div>
      <p className="mt-2 break-words text-sm leading-7 text-[var(--color-text)]">
        {item.label}
      </p>
      <div className="mt-2 flex flex-wrap items-center gap-2">
        {item.sourceAnchors.map((anchor) => (
          <SourceAnchor
            key={`${anchor.case_id}-${anchor.source_chunk_id}`}
            anchor={anchor}
            onOpenSource={onOpenSource}
          />
        ))}
        {primaryAnchor ? (
          <button
            type="button"
            className="inline-flex h-8 items-center justify-center rounded-[8px] border border-[var(--color-border-strong)] bg-[var(--color-surface)] px-3 text-xs font-medium text-[var(--color-brand)] transition hover:bg-[var(--color-brand-soft)] focus:outline-none focus:ring-2 focus:ring-[var(--color-brand-soft)]"
            onClick={() => onOpenSource(primaryAnchor.source_chunk_id)}
          >
            查看来源片段
          </button>
        ) : null}
      </div>
    </article>
  );
}

function SourceEntryList({
  chunks,
  onOpenSource,
}: {
  chunks: CaseChunk[];
  onOpenSource: (chunkId: string) => void;
}) {
  const anchors = chunks
    .map((chunk) => getChunkAnchor(chunk))
    .filter((anchor): anchor is SourceAnchorType => Boolean(anchor));

  if (anchors.length === 0) {
    return null;
  }

  return (
    <div className="mt-3 flex flex-wrap gap-2">
      {anchors.map((anchor) => (
        <SourceAnchor
          key={`${anchor.case_id}-${anchor.source_chunk_id}`}
          anchor={anchor}
          onOpenSource={onOpenSource}
        />
      ))}
    </div>
  );
}

function SourceAnchor({
  anchor,
  onOpenSource,
}: {
  anchor: SourceAnchorType;
  onOpenSource?: (chunkId: string) => void;
}) {
  const title = `case_id: ${anchor.case_id}; source_chunk_id: ${anchor.source_chunk_id}; source: ${
    anchor.source_url || anchor.source_ref || "local_case_store"
  }`;
  const className =
    "max-w-full break-all rounded-[4px] bg-[var(--color-surface-muted)] px-2 py-1 font-mono text-[11px] leading-5 text-[var(--color-text-muted)]";

  if (onOpenSource) {
    return (
      <button
        type="button"
        className={`${className} text-left transition hover:bg-[var(--color-brand-soft)] focus:outline-none focus:ring-2 focus:ring-[var(--color-brand-soft)]`}
        title={title}
        onClick={() => onOpenSource(anchor.source_chunk_id)}
      >
        source_chunk_id: {anchor.source_chunk_id}
      </button>
    );
  }

  return (
    <span
      className={className}
      title={title}
    >
      source_chunk_id: {anchor.source_chunk_id}
    </span>
  );
}

function FallbackNotice({ text }: { text: string }) {
  return (
    <p className="mt-3 rounded-[8px] border border-[var(--color-border)] bg-[var(--color-surface-muted)] px-3 py-2 text-sm leading-6 text-[var(--color-text-muted)]">
      {text}
    </p>
  );
}

function Divider() {
  return <div className="border-t border-[var(--color-border)]" />;
}

function pickSourceChunks({
  chunks,
  seedSourceChunkIds,
}: {
  chunks: CaseChunk[];
  seedSourceChunkIds?: string[];
}) {
  const byId = new Map(chunks.map((chunk) => [chunk.chunk_id, chunk]));
  const selected: CaseChunk[] = [];

  seedSourceChunkIds?.forEach((chunkId) => {
    const chunk = byId.get(chunkId);
    if (chunk && !selected.some((item) => item.chunk_id === chunk.chunk_id)) {
      selected.push(chunk);
    }
  });

  chunks.forEach((chunk) => {
    if (selected.length >= 5) {
      return;
    }

    if (!selected.some((item) => item.chunk_id === chunk.chunk_id)) {
      selected.push(chunk);
    }
  });

  return selected.slice(0, 5);
}

function getVerifiedHoldingSummaryItems(
  detail: CaseDetailResponse
): VerifiedHoldingSummaryItem[] {
  const holdingSummary = detail.holding_summary;
  if (!holdingSummary || holdingSummary.generation_status !== "generated") {
    return [];
  }

  return (holdingSummary.summary_items || [])
    .map((item) => getVerifiedHoldingSummaryItem({ detail, item }))
    .filter((item): item is VerifiedHoldingSummaryItem => Boolean(item));
}

function getVerifiedHoldingSummaryItem({
  detail,
  item,
}: {
  detail: CaseDetailResponse;
  item: HoldingSummaryItem;
}): VerifiedHoldingSummaryItem | null {
  const text = item.text?.trim();
  if (!text) {
    return null;
  }

  const sourceAnchors = (item.source_anchors || []).filter(
    (anchor) =>
      isSourceAnchor(anchor) &&
      anchor.case_id === detail.case_id &&
      detail.chunks.some(
        (chunk) =>
          chunk.chunk_id === anchor.source_chunk_id && hasChunkAnchor(chunk)
      )
  );
  if (sourceAnchors.length === 0) {
    return null;
  }

  return {
    text,
    sourceAnchors,
    confidence: item.confidence,
  };
}

function getVerifiedReadingNavigationItems({
  detail,
  section,
  categories,
}: {
  detail: CaseDetailResponse;
  section?: ReadingNavigationSectionType | null;
  categories: string[];
}): VerifiedReadingNavigationItem[] {
  if (!section || section.generation_status !== "generated") {
    return [];
  }

  const categorySet = new Set(categories);
  return (section.items || [])
    .map((item) =>
      getVerifiedReadingNavigationItem({
        detail,
        item,
        categorySet,
      })
    )
    .filter((item): item is VerifiedReadingNavigationItem => Boolean(item));
}

function getVerifiedReadingNavigationItem({
  detail,
  item,
  categorySet,
}: {
  detail: CaseDetailResponse;
  item: ReadingNavigationItem;
  categorySet: Set<string>;
}): VerifiedReadingNavigationItem | null {
  const label = item.label?.trim();
  const category = item.category?.trim();
  if (
    !label ||
    !category ||
    !categorySet.has(category) ||
    !allowedReadingCategories.has(category) ||
    forbiddenReadingTerms.some((term) => label.includes(term))
  ) {
    return null;
  }

  const sourceAnchors = (item.source_anchors || []).filter(
    (anchor) =>
      isSourceAnchor(anchor) &&
      anchor.case_id === detail.case_id &&
      detail.chunks.some(
        (chunk) =>
          chunk.chunk_id === anchor.source_chunk_id && hasChunkAnchor(chunk)
      )
  );
  if (sourceAnchors.length === 0) {
    return null;
  }

  return {
    label,
    category,
    sourceAnchors,
    confidence: item.confidence,
  };
}

function getVerifiedSummary({
  detail,
  seedResult,
  factChunks,
}: {
  detail: CaseDetailResponse;
  seedResult?: SearchResultItem;
  factChunks: CaseChunk[];
}) {
  const seedSummary = seedResult?.summary;

  if (
    seedSummary?.text?.trim() &&
    seedSummary.source_chunk_id &&
    seedSummary.source_anchors?.some(
      (anchor) =>
        isSourceAnchor(anchor) &&
        anchor.anchor_type === "summary" &&
        anchor.case_id === detail.case_id &&
        anchor.source_chunk_id === seedSummary.source_chunk_id
    ) &&
    detail.chunks.some(
      (chunk) =>
        chunk.chunk_id === seedSummary.source_chunk_id && hasChunkAnchor(chunk)
    )
  ) {
    const anchor = seedSummary.source_anchors.find(
      (item) => item.source_chunk_id === seedSummary.source_chunk_id
    ) as SourceAnchorType;
    return {
      text: seedSummary.text.trim(),
      sourceChunkId: seedSummary.source_chunk_id,
      anchor,
    };
  }

  const fallbackChunk = factChunks[0];
  const fallbackAnchor = fallbackChunk ? getChunkAnchor(fallbackChunk) : null;

  if (fallbackChunk?.text?.trim() && fallbackAnchor) {
    return {
      text: fallbackChunk.text.trim(),
      sourceChunkId: fallbackChunk.chunk_id,
      anchor: fallbackAnchor,
    };
  }

  return null;
}

function hasChunkAnchor(chunk: CaseChunk) {
  return Boolean(getChunkAnchor(chunk));
}

function getChunkAnchor(chunk: CaseChunk) {
  return (chunk.source_anchors || []).find(
    (anchor) =>
      isSourceAnchor(anchor) &&
      anchor.anchor_type === "detail_chunk" &&
      anchor.source_chunk_id === chunk.chunk_id
  );
}

function isSourceAnchor(anchor: SourceAnchorType | undefined | null): anchor is SourceAnchorType {
  return Boolean(anchor?.case_id?.trim() && anchor.source_chunk_id?.trim());
}

function isUsableUrl(value: string) {
  try {
    const url = new URL(value);
    return url.protocol === "http:" || url.protocol === "https:";
  } catch {
    return false;
  }
}

function formatChunkType(chunkType: string) {
  const labels: Record<string, string> = {
    fact: "事实片段",
    court_found: "法院查明",
    court_opinion: "裁判说理",
    judgment_result: "裁判结果",
  };

  return labels[chunkType] || chunkType;
}

function formatHoldingDegradeReason(reason?: string | null) {
  const labels: Record<string, string> = {
    missing_source_anchor: "暂无可核验来源锚点，未展示裁判要旨摘要。",
    insufficient_source: "暂无足够裁判说理来源，未展示裁判要旨摘要。",
    model_failed: "摘要生成暂不可用，已保留来源片段入口供复核。",
    source_mismatch: "来源锚点与片段不一致，未展示裁判要旨摘要。",
  };

  return labels[reason || ""] || "暂无可核验裁判要旨摘要，已保留来源片段入口供复核。";
}

function formatReadingNavigationDegradeReason(reason?: string | null) {
  const labels: Record<string, string> = {
    missing_source_anchor: "暂无可核验来源锚点，未展示争议焦点或关键要素。",
    insufficient_source: "暂无足够来源提炼争议焦点或关键要素，已保留来源片段入口供复核。",
    model_failed: "争议焦点与关键要素暂不可用，已保留来源片段入口供复核。",
    source_mismatch: "来源锚点与片段不一致，未展示争议焦点或关键要素。",
  };

  return labels[reason || ""] || "暂无可核验争议焦点或关键要素，已保留来源片段入口供复核。";
}

function formatConfidence(confidence: string) {
  const labels: Record<string, string> = {
    high: "高",
    medium: "中",
    low: "低",
  };

  return labels[confidence] || confidence;
}
