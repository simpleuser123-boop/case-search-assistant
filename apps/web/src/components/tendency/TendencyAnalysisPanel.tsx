import { useEffect, useState } from "react";

import { isTendencyAnalysisEnabled } from "../../config/featureFlags";
import {
  fetchTendencyAnalysis,
  type TendencyAggregation,
  type TendencyAnalysis,
  type TendencyBucket,
} from "../../services/tendencyApi";

// M5-8 法院/法官倾向分析（F19）展示面板（flag-gated）。
//
// 默认 false（isTendencyAnalysisEnabled=false）时直接渲染 null：不显示任何入口、
// 不调用任何接口，页面回到 M5-7 末态。
//
// 边界（与后端一致，前端只如实展示，不二次推断）：
//   - 只展示只读聚合统计：法院层级/审级/案件领域/案由的分布与占比。
//   - 强制标注样本量与覆盖范围；样本不足的维度/分组明确标注"样本不足"且不解读占比。
//   - 可追溯到来源 case_id（仅引用，非正文）。
//   - 强制免责说明常驻展示。
//   - 绝不展示个案正文；绝不输出个案预测 / 胜负概率 / 确定性法律结论。
//   - 后端门禁未达标或 flag 关闭 → 403，前端展示"暂不可用"，不渲染任何聚合。

function formatPercent(share: number): string {
  return `${(share * 100).toFixed(1)}%`;
}

function BucketRow({ bucket }: { bucket: TendencyBucket }) {
  return (
    <li className="flex flex-col gap-0.5 border-b border-[var(--color-border)] py-1 last:border-b-0">
      <div className="flex items-center justify-between gap-2">
        <span className="text-[var(--color-text)]">{bucket.label}</span>
        <span className="text-xs text-[var(--color-text-muted)]">
          样本 {bucket.sample_size}
          {bucket.sample_sufficient ? `（占比 ${formatPercent(bucket.share)}）` : "（样本不足，不解读占比）"}
        </span>
      </div>
      {bucket.case_id_refs.length > 0 ? (
        <span className="text-[11px] text-[var(--color-text-muted)]">
          来源 case_id（共 {bucket.case_id_total} 条，示例）：{bucket.case_id_refs.slice(0, 5).join("、")}
          {bucket.case_id_total > 5 ? " …" : ""}
        </span>
      ) : null}
    </li>
  );
}

function AggregationBlock({ agg }: { agg: TendencyAggregation }) {
  return (
    <div className="rounded-[4px] border border-[var(--color-border)] px-3 py-2">
      <div className="flex items-center justify-between gap-2">
        <p className="font-medium text-[var(--color-text)]">{agg.name}</p>
        <span className="text-xs text-[var(--color-text-muted)]">维度样本 {agg.sample_size}</span>
      </div>
      <p className="mt-0.5 text-[11px] text-[var(--color-text-muted)]">{agg.confidence_note}</p>
      {agg.insufficient_dimension ? (
        <p className="mt-1 text-xs text-[var(--color-text-muted)]">该维度样本不足，仅作存在性提示。</p>
      ) : (
        <ul className="mt-1.5 text-sm">
          {agg.buckets.map((b) => (
            <BucketRow key={`${agg.dimension}-${b.label}`} bucket={b} />
          ))}
        </ul>
      )}
    </div>
  );
}

export function TendencyAnalysisPanel() {
  const enabled = isTendencyAnalysisEnabled();
  const [data, setData] = useState<TendencyAnalysis | null>(null);
  const [status, setStatus] = useState<"idle" | "loading" | "unavailable" | "error">("idle");

  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    setStatus("loading");
    fetchTendencyAnalysis().then((res) => {
      if (cancelled) return;
      if (res.ok) {
        setData(res.data);
        setStatus("idle");
      } else if (res.reason === "unavailable") {
        setData(null);
        setStatus("unavailable");
      } else {
        setData(null);
        setStatus("error");
      }
    });
    return () => {
      cancelled = true;
    };
  }, [enabled]);

  // 关闭态：不渲染任何 UI。回到 M5-7 末态的硬保证。
  if (!enabled) {
    return null;
  }

  return (
    <section
      aria-label="法院倾向分析"
      className="rounded-[6px] border border-[var(--color-border)] bg-[var(--color-surface)] px-4 py-3 text-sm text-[var(--color-text)]"
    >
      <p className="font-medium">法院倾向分析（统计参考）</p>
      <p className="mt-0.5 text-xs text-[var(--color-text-muted)]">
        基于现有数据覆盖的只读聚合统计，仅展示法院层级 / 审级 / 案件领域 / 案由的历史分布，
        不预测个案结果、不代表任何具名法官或法院的裁判倾向。
      </p>

      {status === "loading" ? (
        <p className="mt-3 text-xs text-[var(--color-text-muted)]">加载中…</p>
      ) : null}

      {status === "unavailable" ? (
        <p className="mt-3 text-xs text-[var(--color-text-muted)]">
          倾向分析暂不可用：数据门禁未达标或该能力未启用，按数据治理边界要求不展示分析。
        </p>
      ) : null}

      {status === "error" ? (
        <p className="mt-3 text-xs text-[var(--color-text-muted)]">倾向分析加载失败，请稍后重试。</p>
      ) : null}

      {data ? (
        <div className="mt-3 flex flex-col gap-2">
          <div className="rounded-[4px] bg-[var(--color-bg)] px-3 py-2 text-xs text-[var(--color-text-muted)]">
            <p>数据来源：{data.data_source}</p>
            <p className="mt-0.5">{data.coverage_range}</p>
            <p className="mt-0.5">
              纳入聚合总样本 {data.total_sample_size} 条；单分组最小可解释样本门槛 {data.min_sample_threshold}。
            </p>
          </div>
          {data.aggregations.map((agg) => (
            <AggregationBlock key={agg.dimension} agg={agg} />
          ))}
          <p className="mt-1 rounded-[4px] border border-dashed border-[var(--color-border)] px-3 py-2 text-[11px] leading-5 text-[var(--color-text-muted)]">
            {data.disclaimer}
          </p>
        </div>
      ) : null}
    </section>
  );
}
