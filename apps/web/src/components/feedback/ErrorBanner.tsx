import { SearchApiError } from "../../services/searchApi";

type ErrorBannerProps = {
  error: Error;
  isRetrying?: boolean;
  onRetry: () => void;
};

export function ErrorBanner({ error, isRetrying = false, onRetry }: ErrorBannerProps) {
  const apiError = error instanceof SearchApiError ? error : null;

  return (
    <div
      role="alert"
      className="rounded-[8px] border border-[var(--color-danger)] bg-[var(--color-danger-soft)] px-4 py-3 text-sm leading-6 text-[var(--color-text)]"
    >
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <p className="font-medium text-[var(--color-danger)]">
            检索请求未完成
          </p>
          <p className="mt-1">
            {apiError?.message || "网络连接异常，请稍后重试。"}
          </p>
          {apiError?.code || apiError?.querySessionId ? (
            <p className="mt-1 font-mono text-xs text-[var(--color-text-muted)]">
              {apiError.code ? `code: ${apiError.code}` : null}
              {apiError.code && apiError.querySessionId ? " / " : null}
              {apiError.querySessionId ? `query_session_id: ${apiError.querySessionId}` : null}
            </p>
          ) : null}
        </div>
        <button
          type="button"
          className="inline-flex h-9 shrink-0 items-center justify-center rounded-[8px] border border-[var(--color-danger)] bg-[var(--color-surface)] px-3 text-sm font-medium text-[var(--color-danger)] transition hover:bg-white focus:outline-none focus:ring-2 focus:ring-[var(--color-danger-soft)] disabled:cursor-not-allowed disabled:opacity-70"
          onClick={onRetry}
          disabled={isRetrying}
        >
          {isRetrying ? "重试中..." : "重试"}
        </button>
      </div>
    </div>
  );
}
