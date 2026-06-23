import { useCallback, useEffect, useRef, useState } from "react";

import {
  copyCitation,
  logCitationCopy,
  type CitationCopyKind,
  type CitationCopyRecord,
  type CitationCopyStatus,
  type CitationCopySurface,
} from "../../lib/citationCopy";

// M3-7: a single, self-contained copy control. It copies metadata only (a case
// number or a basic citation line) to the clipboard, shows a transient status,
// and emits a sanitized log. It holds NO history, writes NO file, and does not
// touch ranking or selection. Status lives only in local component state and
// resets after a short delay.

type CopyCitationButtonProps = {
  record: CitationCopyRecord;
  kind: CitationCopyKind;
  surface: CitationCopySurface;
  label: string;
  // Accessible name; falls back to label when omitted.
  ariaLabel?: string;
  className?: string;
  size?: "sm" | "md";
  // Test seam: inject a clipboard writer / logger if needed.
  writer?: (text: string) => Promise<void>;
};

const STATUS_TEXT: Record<CitationCopyStatus, string> = {
  idle: "",
  copied: "已复制",
  unavailable: "复制不可用，请手动选择文本复制",
  failed: "复制失败，请手动选择文本复制",
};

export function CopyCitationButton({
  record,
  kind,
  surface,
  label,
  ariaLabel,
  className,
  size = "sm",
  writer,
}: CopyCitationButtonProps) {
  const [status, setStatus] = useState<CitationCopyStatus>("idle");
  const resetTimerRef = useRef<number | null>(null);

  useEffect(
    () => () => {
      if (resetTimerRef.current !== null) {
        window.clearTimeout(resetTimerRef.current);
      }
    },
    []
  );

  const handleCopy = useCallback(async () => {
    const outcome = await copyCitation({ record, kind, writer });
    logCitationCopy({ surface, kind, outcome });
    setStatus(outcome.status);

    if (resetTimerRef.current !== null) {
      window.clearTimeout(resetTimerRef.current);
    }
    resetTimerRef.current = window.setTimeout(() => {
      setStatus("idle");
    }, 2400);
  }, [kind, record, surface, writer]);

  const statusText = STATUS_TEXT[status];
  const isError = status === "unavailable" || status === "failed";
  const heightClass = size === "md" ? "h-9" : "h-8";

  return (
    <span className="inline-flex flex-col items-start gap-1">
      <button
        type="button"
        aria-label={ariaLabel || label}
        className={[
          heightClass,
          "inline-flex shrink-0 items-center justify-center rounded-[8px] border border-[var(--color-border-strong)] bg-[var(--color-surface)] px-3 text-xs font-medium text-[var(--color-brand)] transition hover:bg-[var(--color-brand-soft)] focus:outline-none focus:ring-2 focus:ring-[var(--color-brand-soft)]",
          className || "",
        ].join(" ")}
        onClick={(event) => {
          event.stopPropagation();
          void handleCopy();
        }}
      >
        {status === "copied" ? "已复制" : label}
      </button>
      {statusText && status !== "copied" ? (
        <span
          role={isError ? "alert" : undefined}
          className={[
            "text-[11px] leading-4",
            isError ? "text-[var(--color-warning)]" : "text-[var(--color-text-muted)]",
          ].join(" ")}
        >
          {statusText}
        </span>
      ) : (
        <span aria-live="polite" className="sr-only">
          {status === "copied" ? statusText : ""}
        </span>
      )}
    </span>
  );
}
