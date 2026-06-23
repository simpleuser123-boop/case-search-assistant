import type { DataCoverage, SearchResponse, SearchResultSource } from "../../types/search";
import { formatDegradedReason } from "../../lib/searchDisplay";

type ResultOverviewProps = {
  response: SearchResponse;
  source: SearchResultSource;
};

export function ResultOverview({ response, source }: ResultOverviewProps) {
  const resultCount = response.results.length;
  const timings = response.timings;
  const coverage = response.coverage ?? unavailableCoverage();
  const isDegraded = response.degraded || response.degraded_reasons.length > 0;
  const coverageUnavailable = isCoverageUnavailable(coverage);

  return (
    <section
      aria-labelledby="result-overview-title"
      className="rounded-[8px] border border-[var(--color-border)] bg-[var(--color-surface)] p-4 sm:p-5"
    >
      <div className="grid gap-3 md:grid-cols-[1fr_auto] md:items-start">
        <div>
          <p className="text-xs font-medium text-[var(--color-brand)]">
            搜索结果
          </p>
          <h1
            id="result-overview-title"
            className="mt-1 text-xl font-semibold text-[var(--color-text)]"
          >
            找到 {resultCount} 条可复核案例
          </h1>
          <p className="mt-2 text-sm leading-6 text-[var(--color-text-muted)]">
            按事实相似度优先排序。分数只表示检索相关度，不代表案件结果或相关案例完整范围。
          </p>
        </div>

        <div className="grid grid-cols-2 gap-2 text-sm md:min-w-[420px]">
          <Metric label="总耗时" value={formatDuration(timings.total_duration_ms)} />
          <Metric label="候选规模" value={formatCandidateCount(coverage.total_candidate_count)} />
          <Metric label="数据来源" value={formatCoverageText(coverage.data_source, "来源暂不可用")} />
          <Metric label="数据截止" value={formatCoverageText(coverage.data_until, "截止日期暂不可用")} />
          <Metric label="检索模式" value={formatSearchMode(coverage.search_mode, isDegraded)} />
          <Metric label="索引版本" value={formatCoverageText(coverage.index_version, "索引版本暂不可用")} />
        </div>
      </div>

      <div className="mt-4 rounded-[8px] border border-[var(--color-border)] bg-[var(--color-surface-muted)] px-3 py-2 text-sm leading-6 text-[var(--color-text-muted)]">
        {coverageUnavailable ? (
          <p>当前数据覆盖信息暂不可用，已按本次可用检索结果展示。</p>
        ) : (
          <p>
            覆盖信息仅说明本次检索的数据来源、截止字段、索引版本和候选规模。
          </p>
        )}
      </div>

      {source === "mock" ? (
        <div className="mt-4 rounded-[8px] border border-[var(--color-warning)] bg-[var(--color-warning-soft)] px-3 py-2 text-sm leading-6 text-[var(--color-text)]">
          当前使用前端测试数据，所有案例均为非真实样例，仅用于验证页面渲染。
        </div>
      ) : null}

      {isDegraded ? (
        <div className="mt-4 rounded-[8px] border border-[var(--color-warning)] bg-[var(--color-warning-soft)] px-3 py-2 text-sm leading-6 text-[var(--color-text)]">
          <p className="font-medium text-[var(--color-warning)]">
            已使用基础检索
          </p>
          <ul className="mt-1 list-inside list-disc text-[var(--color-text-muted)]">
            {response.degraded_reasons.length > 0 ? (
              response.degraded_reasons.map((reason) => (
                <li key={reason}>{formatDegradedReason(reason)}</li>
              ))
            ) : (
              <li>部分增强能力不可用，结果仍需人工复核。</li>
            )}
          </ul>
        </div>
      ) : null}

      <details className="mt-4 rounded-[8px] bg-[var(--color-surface-muted)] px-3 py-2 text-xs text-[var(--color-text-muted)]">
        <summary className="cursor-pointer font-medium text-[var(--color-text)]">
          阶段耗时与调试字段
        </summary>
        <dl className="mt-2 grid gap-1 sm:grid-cols-2 lg:grid-cols-3">
          <TimingItem label="案情改写" value={timings.rewrite_duration_ms} />
          <TimingItem label="向量生成" value={timings.embedding_duration_ms} />
          <TimingItem label="召回" value={timings.retrieval_duration_ms} />
          <TimingItem label="重排" value={timings.rerank_duration_ms} />
          <TimingItem label="摘要" value={timings.summary_duration_ms} />
          <TimingItem label="总耗时" value={timings.total_duration_ms} />
        </dl>
        <p className="mt-2 font-mono">query_session_id: {response.query_session_id}</p>
      </details>
    </section>
  );
}

function unavailableCoverage(): DataCoverage {
  return {
    data_source: "unavailable",
    data_until: "unknown",
    index_version: "unknown",
    total_candidate_count: null,
    search_mode: "standard",
    degraded_reasons: [
      "DATA_SOURCE_UNAVAILABLE",
      "DATA_UNTIL_UNKNOWN",
      "INDEX_VERSION_UNKNOWN",
    ],
  };
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-[8px] border border-[var(--color-border)] px-3 py-2">
      <p className="text-xs text-[var(--color-text-muted)]">{label}</p>
      <p className="mt-1 font-medium text-[var(--color-text)]">{value}</p>
    </div>
  );
}

function formatCandidateCount(value: number | null | undefined) {
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0) {
    return "候选数暂不可用";
  }

  return `${Math.round(value)} 个候选`;
}

function formatCoverageText(value: string | null | undefined, fallback: string) {
  const normalized = value?.trim();
  if (!normalized || normalized === "unknown" || normalized === "unavailable") {
    return fallback;
  }

  return normalized;
}

function formatSearchMode(value: string | null | undefined, isDegraded: boolean) {
  if (isDegraded) {
    return "基础检索";
  }

  return value === "expanded" ? "扩大复核范围" : "标准检索";
}

function isCoverageUnavailable(coverage: DataCoverage) {
  return (
    coverage.data_source === "unavailable" ||
    coverage.data_until === "unknown" ||
    coverage.index_version === "unknown" ||
    typeof coverage.total_candidate_count !== "number"
  );
}

function TimingItem({ label, value }: { label: string; value: number }) {
  return (
    <div className="flex justify-between gap-3">
      <dt>{label}</dt>
      <dd className="font-mono">{formatDuration(value)}</dd>
    </div>
  );
}

function formatDuration(value: number) {
  return `${Math.max(0, Math.round(value))}ms`;
}
