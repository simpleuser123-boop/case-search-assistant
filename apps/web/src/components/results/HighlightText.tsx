import type { SearchHighlight } from "../../types/search";

type HighlightTextProps = {
  highlight: SearchHighlight;
};

export function HighlightText({ highlight }: HighlightTextProps) {
  const text = String(highlight.text || "").trim();
  const anchor = (highlight.source_anchors || []).find(
    (item) =>
      item.case_id?.trim() &&
      item.source_chunk_id?.trim() &&
      item.source_chunk_id === highlight.source_chunk_id
  );

  if (!text || !anchor) {
    return null;
  }

  return (
    <li className="min-w-0 rounded-[6px] border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2">
      <mark
        className="break-words bg-[var(--color-highlight-soft)] text-[var(--color-text)]"
        title={`来源片段 ${anchor.source_chunk_id}`}
      >
        {text}
      </mark>
      <span
        className="mt-1 block max-w-full overflow-hidden text-ellipsis whitespace-nowrap rounded-[4px] bg-[var(--color-surface-muted)] px-1.5 py-0.5 font-mono text-[11px] text-[var(--color-text-muted)]"
        title={`case_id: ${anchor.case_id}; source_chunk_id: ${anchor.source_chunk_id}`}
      >
        {anchor.source_chunk_id}
      </span>
    </li>
  );
}
