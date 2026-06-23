type EmptyResultsProps = {
  onEdit: () => void;
  onExpand: () => void;
  canExpand?: boolean;
  isExpandLoading?: boolean;
};

export function EmptyResults({
  onEdit,
  onExpand,
  canExpand = true,
  isExpandLoading = false,
}: EmptyResultsProps) {
  return (
    <section className="rounded-[8px] border border-[var(--color-border)] bg-[var(--color-surface)] p-5 text-sm leading-6 text-[var(--color-text)]">
      <h2 className="text-base font-semibold">未找到足够匹配的案例</h2>
      <p className="mt-2 text-[var(--color-text-muted)]">
        可以尝试补充案件经过、损害结果、争议焦点，或简化为最核心的事实动作。
      </p>
      <div className="mt-4 flex flex-col gap-2 sm:flex-row">
        <button
          type="button"
          className="inline-flex h-9 items-center justify-center rounded-[8px] bg-[var(--color-brand)] px-3 text-sm font-medium text-white transition hover:bg-[var(--color-brand-hover)] focus:outline-none focus:ring-2 focus:ring-[var(--color-brand-soft)]"
          onClick={onEdit}
        >
          修改描述
        </button>
        {canExpand ? (
          <button
            type="button"
            disabled={isExpandLoading}
            className="inline-flex h-9 items-center justify-center rounded-[8px] border border-[var(--color-warning)] bg-[var(--color-warning-soft)] px-3 text-sm font-medium text-[var(--color-warning)] transition hover:bg-white focus:outline-none focus:ring-2 focus:ring-[var(--color-brand-soft)] disabled:cursor-not-allowed disabled:opacity-70"
            onClick={onExpand}
          >
            {isExpandLoading ? "正在扩大复核范围..." : "扩大复核范围"}
          </button>
        ) : null}
        <a
          href="/"
          className="inline-flex h-9 items-center justify-center rounded-[8px] border border-[var(--color-border)] px-3 text-sm font-medium text-[var(--color-text-muted)] transition hover:bg-[var(--color-surface-muted)]"
        >
          查看示例案情
        </a>
      </div>
    </section>
  );
}
