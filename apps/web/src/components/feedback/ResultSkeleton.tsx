export function ResultSkeleton() {
  return (
    <div className="space-y-3" aria-label="正在理解案情">
      <div className="rounded-[8px] border border-[var(--color-border)] bg-[var(--color-surface)] p-4 sm:p-5">
        <div className="h-4 w-32 animate-pulse rounded bg-[var(--color-surface-muted)]" />
        <div className="mt-3 h-7 w-56 animate-pulse rounded bg-[var(--color-surface-muted)]" />
        <div className="mt-4 grid gap-2 sm:grid-cols-3">
          <div className="h-14 animate-pulse rounded-[8px] bg-[var(--color-surface-muted)]" />
          <div className="h-14 animate-pulse rounded-[8px] bg-[var(--color-surface-muted)]" />
          <div className="h-14 animate-pulse rounded-[8px] bg-[var(--color-surface-muted)]" />
        </div>
      </div>

      {[0, 1, 2, 3].map((item) => (
        <article
          key={item}
          className="rounded-[8px] border border-[var(--color-border)] bg-[var(--color-surface)] p-4 sm:p-5"
        >
          <div className="flex flex-col gap-4 lg:flex-row lg:justify-between">
            <div className="flex-1 space-y-3">
              <div className="h-5 w-4/5 animate-pulse rounded bg-[var(--color-surface-muted)]" />
              <div className="h-4 w-3/5 animate-pulse rounded bg-[var(--color-surface-muted)]" />
            </div>
            <div className="h-9 w-full max-w-[220px] animate-pulse rounded bg-[var(--color-surface-muted)]" />
          </div>
          <div className="mt-5 space-y-2">
            <div className="h-4 w-full animate-pulse rounded bg-[var(--color-surface-muted)]" />
            <div className="h-4 w-11/12 animate-pulse rounded bg-[var(--color-surface-muted)]" />
            <div className="h-4 w-4/6 animate-pulse rounded bg-[var(--color-surface-muted)]" />
          </div>
        </article>
      ))}
    </div>
  );
}
