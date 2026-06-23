import { useState } from "react";
import { Link } from "react-router-dom";

import { isStatuteSearchEnabled } from "../config/featureFlags";
import { redactPII, QUERY_TEXT_MAX_LEN } from "../intake/sanitize";
import {
  submitStatuteSearch,
  fetchCasesByStatute,
  type StatuteSearchResult,
  type StatuteCasesResult,
  type StatuteRefView,
  type StatuteCandidateRefView,
} from "../services/statuteApi";

// 法条检索页：本地查询 -> 法条命中（每条带 text_id 来源锚点）-> 法条↔类案互跳。
// 查询态只存本组件 React state（内存），绝不写入任何浏览器存储或 URL query string。
// 条文只渲染后端返回的、带锚点的 article_text；前端不生成 / 不补全 / 不改写任何条文。

type StatuteStatus =
  | { kind: "idle" }
  | { kind: "submitting" }
  | { kind: "error"; message: string }
  | { kind: "done" };

const DISABLED_REASON_TEXT: Record<string, string> = {
  STATUTE_SEARCH_DISABLED: "法条检索当前未启用（后端 ENABLE_STATUTE_SEARCH=false）。",
};

// 命中是否「可展示」：必须带至少一个非空 text_id 锚点（与后端「无锚点不返回」一致）。
// 这是前端最后一道闸——即便后端异常回了无锚点项，前端也不展示其条文。
export function hasDisplayableAnchor(ref: StatuteRefView): boolean {
  return (
    Array.isArray(ref.statute_anchors) &&
    ref.statute_anchors.some((a) => typeof a?.text_id === "string" && a.text_id.trim().length > 0)
  );
}

export function StatutePage() {
  // flag off：DOM 不渲染任何法条检索入口/页面（安全末态）。
  if (!isStatuteSearchEnabled()) {
    return null;
  }
  return <StatuteWorkspace />;
}

function StatuteWorkspace() {
  // 查询要素：仅内存 state，绝不入存储 / 不入 URL。
  const [queryText, setQueryText] = useState("");
  const [caseCause, setCaseCause] = useState("");
  const [region, setRegion] = useState("");

  const [status, setStatus] = useState<StatuteStatus>({ kind: "idle" });
  const [statuteRefs, setStatuteRefs] = useState<StatuteRefView[]>([]);
  const [degradedReasons, setDegradedReasons] = useState<string[]>([]);
  const [searched, setSearched] = useState(false);

  const canSearch = queryText.trim().length > 0;

  // 提交：逐字段构造已脱敏 SearchProfile（对查询做防御性脱敏），只发白名单字段。
  async function handleSearch() {
    if (!canSearch) {
      return;
    }
    setStatus({ kind: "submitting" });
    const profile = {
      case_cause: caseCause.trim() ? redactPII(caseCause.trim()) : null,
      region: region.trim() ? redactPII(region.trim()) : null,
      trial_level_preference: null,
      dispute_focus_keywords: [],
      query_text: redactPII(queryText.trim()).slice(0, QUERY_TEXT_MAX_LEN),
    };
    const result: StatuteSearchResult = await submitStatuteSearch(profile, {
      mode: "standard",
      limit: 10,
    });
    setSearched(true);
    if (result.ok) {
      // 前端最后一道闸：只保留带 text_id 锚点的命中。
      setStatuteRefs((result.data.statute_refs ?? []).filter(hasDisplayableAnchor));
      setDegradedReasons(result.data.degraded_reasons ?? []);
      setStatus({ kind: "done" });
      return;
    }
    setStatuteRefs([]);
    setDegradedReasons([]);
    setStatus({ kind: "error", message: describeError(result) });
  }

  function handleReset() {
    setQueryText("");
    setCaseCause("");
    setRegion("");
    setStatuteRefs([]);
    setDegradedReasons([]);
    setSearched(false);
    setStatus({ kind: "idle" });
  }

  const submitting = status.kind === "submitting";

  return (
    <main
      aria-label="法条法规检索"
      className="min-h-[100dvh] bg-[var(--color-bg)] px-4 py-6 text-[var(--color-text)] sm:px-6 sm:py-10"
    >
      <div className="mx-auto flex w-full max-w-[1180px] flex-col gap-8">
        <header className="flex items-start justify-between border-b border-[var(--color-border)] pb-4">
          <div>
            <p className="text-base font-semibold text-[var(--color-text)]">
              法条法规检索
            </p>
            <p className="mt-1 text-xs text-[var(--color-text-muted)]">
              命中条文均来自法条语料、带可核验来源；支持法条↔类案互跳
            </p>
          </div>
          <Link
            to="/"
            className="hidden rounded-[8px] border border-[var(--color-border-strong)] px-3 py-1.5 text-xs font-medium text-[var(--color-brand)] transition hover:bg-[var(--color-brand-soft)] sm:inline-flex"
          >
            返回检索助手
          </Link>
        </header>

        <SearchStage
          queryText={queryText}
          onQueryTextChange={setQueryText}
          caseCause={caseCause}
          onCaseCauseChange={setCaseCause}
          region={region}
          onRegionChange={setRegion}
          canSearch={canSearch}
          submitting={submitting}
          onSearch={handleSearch}
          onReset={handleReset}
        />

        {status.kind === "error" ? (
          <p
            role="alert"
            className="rounded-[8px] border border-[var(--color-danger)] bg-[var(--color-danger-soft)] px-3 py-2 text-sm text-[var(--color-danger)]"
          >
            {status.message}
          </p>
        ) : null}

        {searched && status.kind !== "error" ? (
          <ResultsStage statuteRefs={statuteRefs} degradedReasons={degradedReasons} />
        ) : null}

        <footer className="border-t border-[var(--color-border)] pt-4 text-xs leading-5 text-[var(--color-text-muted)]">
          查询要素只保存在本浏览器内存、不写入本地存储、不随 URL 携带；仅脱敏后的检索要素离开浏览器。展示的法条条文均来自法条语料库、带 text_id 来源锚点，本工具不生成或改写任何条文。
        </footer>
      </div>
    </main>
  );
}

function describeError(result: Extract<StatuteSearchResult, { ok: false }>): string {
  if (result.reason === "disabled") {
    return (
      (result.reasonCode && DISABLED_REASON_TEXT[result.reasonCode]) ||
      "法条检索当前未启用，请稍后再试或联系管理员。"
    );
  }
  if (result.reason === "timeout") {
    return "检索请求超时，请稍后重试。";
  }
  if (result.reason === "rejected") {
    return result.message || "提交内容未通过校验，请调整后重试。";
  }
  return "检索服务暂时不可用，请稍后重试。";
}

function SearchStage({
  queryText,
  onQueryTextChange,
  caseCause,
  onCaseCauseChange,
  region,
  onRegionChange,
  canSearch,
  submitting,
  onSearch,
  onReset,
}: {
  queryText: string;
  onQueryTextChange: (value: string) => void;
  caseCause: string;
  onCaseCauseChange: (value: string) => void;
  region: string;
  onRegionChange: (value: string) => void;
  canSearch: boolean;
  submitting: boolean;
  onSearch: () => void;
  onReset: () => void;
}) {
  return (
    <section
      aria-label="法条检索输入"
      className="flex flex-col gap-5 rounded-[10px] border border-[var(--color-border)] bg-[var(--color-surface)] p-5 sm:p-6"
    >
      <div>
        <label
          htmlFor="statute-query"
          className="text-sm font-medium text-[var(--color-text)]"
        >
          检索内容（法律问题 / 争议焦点）
        </label>
        <p className="mt-1 text-xs text-[var(--color-text-muted)]">
          输入法律问题或争议焦点；查询只发脱敏后的检索要素，原始文本不写入存储或 URL。
        </p>
        <textarea
          id="statute-query"
          value={queryText}
          disabled={submitting}
          onChange={(event) => onQueryTextChange(event.target.value)}
          className="mt-2 block min-h-[88px] max-h-[200px] w-full resize-y rounded-[8px] border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-base leading-6 text-[var(--color-text)] outline-none transition focus:border-[var(--color-brand)] focus:ring-2 focus:ring-[var(--color-brand-soft)] disabled:bg-[var(--color-surface-muted)]"
          placeholder="例如：买卖合同中标的物质量不符合约定的违约责任如何认定"
        />
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        <div>
          <label
            htmlFor="statute-case-cause"
            className="text-xs font-medium text-[var(--color-text-muted)]"
          >
            案由（可选）
          </label>
          <input
            id="statute-case-cause"
            value={caseCause}
            disabled={submitting}
            onChange={(event) => onCaseCauseChange(event.target.value)}
            className="mt-1 block h-10 w-full rounded-[8px] border border-[var(--color-border)] bg-[var(--color-surface)] px-3 text-sm text-[var(--color-text)] outline-none transition focus:border-[var(--color-brand)] focus:ring-2 focus:ring-[var(--color-brand-soft)] disabled:bg-[var(--color-surface-muted)]"
            placeholder="如：买卖合同纠纷"
          />
        </div>
        <div>
          <label
            htmlFor="statute-region"
            className="text-xs font-medium text-[var(--color-text-muted)]"
          >
            地域（可选）
          </label>
          <input
            id="statute-region"
            value={region}
            disabled={submitting}
            onChange={(event) => onRegionChange(event.target.value)}
            className="mt-1 block h-10 w-full rounded-[8px] border border-[var(--color-border)] bg-[var(--color-surface)] px-3 text-sm text-[var(--color-text)] outline-none transition focus:border-[var(--color-brand)] focus:ring-2 focus:ring-[var(--color-brand-soft)] disabled:bg-[var(--color-surface-muted)]"
            placeholder="如：北京"
          />
        </div>
      </div>

      <div className="flex flex-col gap-3 border-t border-[var(--color-border)] pt-4 sm:flex-row sm:items-center sm:justify-between">
        <button
          type="button"
          onClick={onReset}
          disabled={submitting}
          className="inline-flex h-10 items-center justify-center rounded-[8px] border border-[var(--color-border-strong)] px-4 text-sm font-medium text-[var(--color-text-muted)] transition hover:bg-[var(--color-surface-muted)] disabled:cursor-not-allowed disabled:opacity-50"
        >
          重置
        </button>
        <button
          type="button"
          onClick={onSearch}
          disabled={!canSearch || submitting}
          className="inline-flex h-10 items-center justify-center rounded-[8px] bg-[var(--color-brand)] px-5 text-sm font-medium text-white transition hover:bg-[var(--color-brand-hover)] focus:outline-none focus:ring-2 focus:ring-[var(--color-brand-soft)] disabled:cursor-not-allowed disabled:opacity-50"
        >
          {submitting ? "检索中…" : "检索法条"}
        </button>
      </div>
    </section>
  );
}

function ResultsStage({
  statuteRefs,
  degradedReasons,
}: {
  statuteRefs: StatuteRefView[];
  degradedReasons: string[];
}) {
  return (
    <section aria-label="法条命中结果" className="flex flex-col gap-4">
      <p className="text-sm font-medium text-[var(--color-text)]">
        法条命中（{statuteRefs.length}）
      </p>

      {degradedReasons.length > 0 ? (
        <p className="rounded-[8px] border border-[var(--color-warning)] bg-[var(--color-warning-soft)] px-3 py-2 text-xs text-[var(--color-warning)]">
          本次检索为降级结果：{degradedReasons.join("、")}
        </p>
      ) : null}

      {statuteRefs.length === 0 ? (
        <p className="rounded-[10px] border border-[var(--color-border)] bg-[var(--color-surface)] px-4 py-6 text-center text-sm text-[var(--color-text-muted)]">
          未命中可锚定来源的法条。可调整检索内容后重试。
        </p>
      ) : (
        <ul className="grid gap-3">
          {statuteRefs.map((ref, index) => (
            <StatuteCard key={`${ref.statute_id}-${index}`} statute={ref} index={index} />
          ))}
        </ul>
      )}
    </section>
  );
}

type CrosslinkStatus =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "done" };

function StatuteCard({ statute, index }: { statute: StatuteRefView; index: number }) {
  // 互跳态：法条→类案的 CandidateRef，只存内存；不写存储 / 不入 URL。
  const [crosslinkStatus, setCrosslinkStatus] = useState<CrosslinkStatus>({ kind: "idle" });
  const [relatedCases, setRelatedCases] = useState<StatuteCandidateRefView[]>([]);
  const [expanded, setExpanded] = useState(false);

  // 锚点：必带 text_id（上层 hasDisplayableAnchor 已过滤，这里再取非空 text_id 展示）。
  const anchors = (statute.statute_anchors || [])
    .filter((a) => typeof a?.text_id === "string" && a.text_id.trim().length > 0)
    .slice(0, 6);

  // 优先用响应内联的 related_case_refs；否则点击时按 statute_id 调 cases-by-statute。
  const inlineRelated = (statute.related_case_refs || []).filter(
    (r) => Array.isArray(r.source_anchors) && r.source_anchors.length > 0,
  );

  async function handleLoadCrosslink() {
    if (expanded) {
      setExpanded(false);
      return;
    }
    setExpanded(true);
    if (inlineRelated.length > 0) {
      setRelatedCases(inlineRelated);
      setCrosslinkStatus({ kind: "done" });
      return;
    }
    setCrosslinkStatus({ kind: "loading" });
    const result: StatuteCasesResult = await fetchCasesByStatute(statute.statute_id, {
      mode: "standard",
      limit: 10,
    });
    if (result.ok) {
      // 只展示带来源锚点的 CandidateRef（无正文，白名单字段 + 锚点）。
      setRelatedCases(
        (result.data.candidate_refs ?? []).filter(
          (r) => Array.isArray(r.source_anchors) && r.source_anchors.length > 0,
        ),
      );
      setCrosslinkStatus({ kind: "done" });
      return;
    }
    setRelatedCases([]);
    setCrosslinkStatus({
      kind: "error",
      message:
        result.reason === "disabled"
          ? "法条检索未启用，无法加载关联类案。"
          : "关联类案加载失败，请稍后重试。",
    });
  }

  return (
    <li className="rounded-[8px] border border-[var(--color-border)] bg-[var(--color-surface)] p-4 sm:p-5">
      <div className="flex items-start gap-2">
        <span className="mt-0.5 shrink-0 rounded-[4px] bg-[var(--color-brand-soft)] px-2 py-1 text-xs font-medium text-[var(--color-brand)]">
          #{index + 1}
        </span>
        <div className="min-w-0">
          <p className="break-words text-sm font-medium text-[var(--color-text)]">
            {statute.law_name}
            {statute.article_no ? (
              <span className="ml-2 font-mono text-[var(--color-text-muted)]">
                {statute.article_no}
              </span>
            ) : null}
          </p>
          <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-xs leading-5 text-[var(--color-text-muted)]">
            <span className="font-mono">{statute.statute_id}</span>
            {statute.source_corpus ? <span>语料：{statute.source_corpus}</span> : null}
            {statute.effective_status ? <span>{statute.effective_status}</span> : null}
          </div>
        </div>
      </div>

      {/* 条文：只渲染后端返回的、带锚点的 article_text；前端不生成/补全/改写条文。 */}
      {statute.article_text ? (
        <p className="mt-3 whitespace-pre-wrap break-words rounded-[8px] border border-[var(--color-border)] bg-[var(--color-surface-muted)] px-3 py-2 text-sm leading-6 text-[var(--color-text)]">
          {statute.article_text}
        </p>
      ) : (
        <p className="mt-3 text-xs text-[var(--color-text-subtle)]">
          该命中暂无可展示的语料条文文本（仅展示带锚点的来源标识）。
        </p>
      )}

      {/* 来源锚点：必带 text_id，作为可核验出处。 */}
      <div className="mt-3 flex flex-wrap gap-2 border-t border-[var(--color-border)] pt-3">
        <span className="text-xs text-[var(--color-text-muted)]">来源出处：</span>
        {anchors.map((anchor) => (
          <span
            key={anchor.text_id}
            className="max-w-full truncate rounded-[4px] bg-[var(--color-surface-muted)] px-2 py-1 font-mono text-[11px] text-[var(--color-text-muted)]"
            title={`text_id: ${anchor.text_id}${anchor.law_name ? `; ${anchor.law_name}` : ""}${anchor.article_no ? ` ${anchor.article_no}` : ""}`}
          >
            {anchor.text_id}
          </span>
        ))}
      </div>

      <div className="mt-3 flex flex-col gap-3 border-t border-[var(--color-border)] pt-3">
        <button
          type="button"
          onClick={handleLoadCrosslink}
          className="inline-flex h-9 w-fit items-center justify-center rounded-[8px] border border-[var(--color-border-strong)] bg-[var(--color-surface)] px-3 text-sm font-medium text-[var(--color-brand)] transition hover:bg-[var(--color-brand-soft)] focus:outline-none focus:ring-2 focus:ring-[var(--color-brand-soft)]"
        >
          {expanded ? "收起关联类案" : "查看引用本法条的类案"}
        </button>

        {expanded ? (
          <RelatedCases status={crosslinkStatus} relatedCases={relatedCases} />
        ) : null}
      </div>
    </li>
  );
}

function RelatedCases({
  status,
  relatedCases,
}: {
  status: CrosslinkStatus;
  relatedCases: StatuteCandidateRefView[];
}) {
  if (status.kind === "loading") {
    return (
      <p className="text-xs text-[var(--color-text-muted)]">关联类案加载中…</p>
    );
  }
  if (status.kind === "error") {
    return (
      <p role="alert" className="text-xs text-[var(--color-danger)]">
        {status.message}
      </p>
    );
  }
  if (relatedCases.length === 0) {
    return (
      <p className="text-xs text-[var(--color-text-subtle)]">
        暂无可锚定来源的关联类案。
      </p>
    );
  }
  return (
    <ul className="grid gap-2">
      {relatedCases.map((ref, index) => (
        <RelatedCaseRow key={`${ref.case_id}-${index}`} refItem={ref} />
      ))}
    </ul>
  );
}

function RelatedCaseRow({ refItem }: { refItem: StatuteCandidateRefView }) {
  const meta = [
    refItem.case_number || "案号暂缺",
    refItem.court || "法院暂缺",
    refItem.trial_level || "审级暂缺",
    refItem.case_cause || "案由暂缺",
    refItem.judgment_date || "日期暂缺",
  ];
  const anchors = (refItem.source_anchors || []).slice(0, 3);

  return (
    <li className="rounded-[8px] border border-[var(--color-border)] bg-[var(--color-surface-muted)] p-3">
      <p className="break-words font-mono text-sm text-[var(--color-text)]">
        {refItem.case_id}
      </p>
      <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-xs leading-5 text-[var(--color-text-muted)]">
        {meta.map((item) => (
          <span key={item}>{item}</span>
        ))}
      </div>
      <div className="mt-2 flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex min-w-0 flex-wrap gap-2">
          {anchors.length > 0 ? (
            anchors.map((anchor) => (
              <span
                key={`${anchor.case_id}-${anchor.source_chunk_id}`}
                className="max-w-full truncate rounded-[4px] bg-[var(--color-surface)] px-2 py-1 font-mono text-[11px] text-[var(--color-text-muted)]"
                title={`case_id: ${anchor.case_id}; source_chunk_id: ${anchor.source_chunk_id}`}
              >
                来源 {anchor.source_chunk_id}
              </span>
            ))
          ) : (
            <span className="text-xs text-[var(--color-text-subtle)]">来源片段暂缺</span>
          )}
        </div>
        <Link
          to={`/?case=${encodeURIComponent(refItem.case_id)}`}
          className="inline-flex h-8 shrink-0 items-center justify-center rounded-[8px] border border-[var(--color-border-strong)] bg-[var(--color-surface)] px-3 text-xs font-medium text-[var(--color-brand)] transition hover:bg-[var(--color-brand-soft)] focus:outline-none focus:ring-2 focus:ring-[var(--color-brand-soft)]"
        >
          在检索助手中查看
        </Link>
      </div>
    </li>
  );
}
