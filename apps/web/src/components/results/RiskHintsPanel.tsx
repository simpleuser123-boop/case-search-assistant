import type { RiskHint, SearchResponse, SearchResultItem, SourceAnchor } from "../../types/search";

type RiskHintsPanelProps = {
  responses: SearchResponse[];
  onSelectResult: (result: SearchResultItem, triggerElement: HTMLElement) => void;
};

type VisibleRiskHint = {
  hint: RiskHint;
  anchor: SourceAnchor;
  result: SearchResultItem;
};

export function RiskHintsPanel({ responses, onSelectResult }: RiskHintsPanelProps) {
  const visibleHints = collectVisibleRiskHints(responses);

  if (visibleHints.length === 0) {
    return null;
  }

  return (
    <section
      aria-labelledby="risk-hints-heading"
      className="rounded-[8px] border border-[var(--color-warning)] bg-[var(--color-warning-soft)] px-4 py-4 sm:px-5"
    >
      <div className="flex flex-col gap-2">
        <p className="text-xs font-medium text-[var(--color-warning)]">
          供复核
        </p>
        <h2 id="risk-hints-heading" className="text-base font-semibold text-[var(--color-text)]">
          复核风险提示
        </h2>
        <p className="text-sm leading-6 text-[var(--color-text-muted)]">
          以下仅为有来源锚点的复核线索，不影响主结果排序。
        </p>
      </div>

      <ul className="mt-3 grid gap-2">
        {visibleHints.map(({ hint, anchor, result }, index) => (
          <li
            key={`${hint.risk_type}-${anchor.case_id}-${anchor.source_chunk_id}-${index}`}
            className="rounded-[8px] border border-[var(--color-border)] bg-white px-3 py-3"
          >
            <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="rounded-[4px] border border-[var(--color-warning)] px-2 py-1 text-[11px] font-medium text-[var(--color-warning)]">
                    {formatRiskType(hint.risk_type)}
                  </span>
                  <span className="rounded-[4px] bg-[var(--color-surface-muted)] px-2 py-1 font-mono text-[11px] text-[var(--color-text-muted)]">
                    {hint.reason_code}
                  </span>
                </div>
                <p className="mt-2 text-sm leading-6 text-[var(--color-text)]">
                  {formatReviewNote(hint.risk_type)}
                </p>
                <p className="mt-1 break-all font-mono text-[11px] leading-5 text-[var(--color-text-muted)]">
                  case_id: {anchor.case_id}; source_chunk_id: {anchor.source_chunk_id}
                </p>
              </div>

              <button
                type="button"
                className="inline-flex h-9 shrink-0 items-center justify-center rounded-[8px] border border-[var(--color-warning)] bg-[var(--color-surface)] px-3 text-sm font-medium text-[var(--color-warning)] transition hover:bg-white focus:outline-none focus:ring-2 focus:ring-[var(--color-brand-soft)]"
                onClick={(event) => onSelectResult(result, event.currentTarget)}
              >
                查看来源
              </button>
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}

function collectVisibleRiskHints(responses: SearchResponse[]): VisibleRiskHint[] {
  const resultByCaseId = new Map<string, SearchResultItem>();
  responses.forEach((response) => {
    [...response.results, ...response.low_confidence_candidates].forEach((result) => {
      if (!resultByCaseId.has(result.case_id)) {
        resultByCaseId.set(result.case_id, result);
      }
    });
  });

  const visible: VisibleRiskHint[] = [];
  const seen = new Set<string>();

  responses.forEach((response) => {
    (response.risk_hints || []).forEach((hint) => {
      const anchor = (hint.source_anchors || []).find(isSourceAnchor);
      if (!anchor) {
        return;
      }

      const result = resultByCaseId.get(anchor.case_id);
      if (!result) {
        return;
      }

      const key = `${hint.risk_type}:${anchor.case_id}:${anchor.source_chunk_id}:${hint.reason_code}`;
      if (seen.has(key)) {
        return;
      }

      seen.add(key);
      visible.push({ hint, anchor, result });
    });
  });

  return visible.slice(0, 5);
}

function isSourceAnchor(anchor: SourceAnchor | undefined | null): anchor is SourceAnchor {
  return Boolean(anchor?.case_id?.trim() && anchor.source_chunk_id?.trim());
}

function formatRiskType(riskType: string) {
  const labels: Record<string, string> = {
    fact_difference: "事实差异",
    key_element_missing: "关键要素缺失",
    low_confidence_candidate: "低置信度候选",
    adverse_tendency_source: "不利倾向来源",
    degraded_or_uncertain: "降级或不确定",
  };

  return labels[riskType] || "复核线索";
}

function formatReviewNote(riskType: string) {
  const notes: Record<string, string> = {
    fact_difference: "该来源片段与当前案情可能存在事实差异，建议律师结合原文复核。",
    key_element_missing: "该候选命中的关键要素较少，供复核是否缺少必要事实。",
    low_confidence_candidate: "该候选为低置信度结果，仅作为补充复核线索。",
    adverse_tendency_source: "该来源片段可能包含不利倾向线索，建议结合原文复核。",
    degraded_or_uncertain: "本次检索存在降级或不确定状态，建议结合来源片段复核。",
  };

  return notes[riskType] || "该线索仅供复核，请结合来源片段判断。";
}
