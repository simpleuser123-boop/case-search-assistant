type SimilarityMeterProps = {
  score?: number | null;
};

export function SimilarityMeter({ score }: SimilarityMeterProps) {
  const percent = toPercent(score);
  const color = getMeterColor(percent);

  return (
    <div className="w-full max-w-[220px]" aria-label={formatScoreText(percent)}>
      <div className="mb-1 flex items-center justify-between gap-2">
        <span className="text-xs font-medium text-[var(--color-text)]">
          {formatScoreText(percent)}
        </span>
        <span className="text-[11px] text-[var(--color-text-subtle)]">
          仅代表检索相关度
        </span>
      </div>
      <div className="h-2 w-full overflow-hidden rounded-[4px] bg-[var(--color-surface-muted)]">
        <div
          className="h-full rounded-[4px]"
          style={{ width: `${percent ?? 0}%`, backgroundColor: color }}
        />
      </div>
    </div>
  );
}

function toPercent(score?: number | null) {
  if (typeof score !== "number" || Number.isNaN(score)) {
    return null;
  }

  const normalized = score > 1 ? score : score * 100;
  return Math.max(0, Math.min(100, Math.round(normalized)));
}

function formatScoreText(percent: number | null) {
  return percent === null ? "事实相似度暂无评分" : `事实相似度 ${percent}%`;
}

function getMeterColor(percent: number | null) {
  if (percent === null) {
    return "var(--color-border-strong)";
  }

  if (percent >= 80) {
    return "var(--color-brand)";
  }

  if (percent >= 65) {
    return "var(--color-warning)";
  }

  return "#60758f";
}
