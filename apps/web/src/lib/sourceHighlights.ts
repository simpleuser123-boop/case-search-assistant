import type { SourceAnchor } from "../types/search";

// M3-5 highlight navigation: locate a source chunk from a reading-assist module.
// This layer never rewrites body text and never emits any legal conclusion. It
// only resolves whether a source chunk can be navigated to, and surfaces a safe
// degraded state (with a sanitized reason code) when it cannot.

export type HighlightRelatedModule =
  | "holding_summary"
  | "issue_focus"
  | "key_elements"
  | "fact_alignment";

export type HighlightNavigationStatus = "navigated" | "degraded";

export type HighlightDegradeReason =
  | "missing_source_anchor"
  | "source_chunk_unavailable"
  | "highlight_target_missing"
  | "navigation_failed";

export type HighlightNavigationResult = {
  status: HighlightNavigationStatus;
  reason?: HighlightDegradeReason;
  anchorType?: string;
};

export const HIGHLIGHT_DEGRADE_REASONS: readonly HighlightDegradeReason[] = [
  "missing_source_anchor",
  "source_chunk_unavailable",
  "highlight_target_missing",
  "navigation_failed",
];

export function sourceChunkElementId(chunkId: string) {
  return `source-chunk-${encodeURIComponent(chunkId)}`;
}

type ScrollableTarget = {
  scrollIntoView: (options?: ScrollIntoViewOptions) => void;
};

type ResolveTargetFn = (elementId: string) => ScrollableTarget | null;

/**
 * Resolve and perform highlight navigation to a source chunk.
 *
 * Pure with respect to the injected `resolveTarget` (defaults to DOM lookup),
 * so it is unit-testable without a browser. Any failure degrades safely and is
 * reported through the return value; it never throws to the caller.
 */
export function navigateToSourceChunk({
  chunkId,
  anchorType,
  resolveTarget = defaultResolveTarget,
}: {
  chunkId: string | null | undefined;
  anchorType?: string;
  resolveTarget?: ResolveTargetFn;
}): HighlightNavigationResult {
  const normalizedChunkId = (chunkId || "").trim();
  if (!normalizedChunkId) {
    return { status: "degraded", reason: "missing_source_anchor", anchorType };
  }

  let target: ScrollableTarget | null = null;
  try {
    target = resolveTarget(sourceChunkElementId(normalizedChunkId));
  } catch {
    return { status: "degraded", reason: "navigation_failed", anchorType };
  }

  if (!target) {
    return {
      status: "degraded",
      reason: "highlight_target_missing",
      anchorType,
    };
  }

  try {
    target.scrollIntoView({ block: "start", behavior: "smooth" });
  } catch {
    return { status: "degraded", reason: "navigation_failed", anchorType };
  }

  return { status: "navigated", anchorType };
}

/**
 * Decide whether an anchor is a usable highlight target before render time.
 * Mirrors the backend contract: anchor must carry case_id + source_chunk_id and
 * the chunk must be a navigable (anchored, non-empty) source excerpt.
 */
export function resolveHighlightDisplay({
  anchor,
  navigableChunkIds,
}: {
  anchor: Pick<SourceAnchor, "case_id" | "source_chunk_id"> | null | undefined;
  navigableChunkIds: ReadonlySet<string>;
}): {
  displayStatus: "available" | "degraded";
  degradeReason?: HighlightDegradeReason;
} {
  const caseId = (anchor?.case_id || "").trim();
  const chunkId = (anchor?.source_chunk_id || "").trim();
  if (!caseId || !chunkId) {
    return { displayStatus: "degraded", degradeReason: "missing_source_anchor" };
  }
  if (!navigableChunkIds.has(chunkId)) {
    return {
      displayStatus: "degraded",
      degradeReason: "source_chunk_unavailable",
    };
  }
  return { displayStatus: "available" };
}

const FALLBACK_DEGRADE_REASON: HighlightDegradeReason = "navigation_failed";

/**
 * Emit a sanitized highlight navigation log line. Records only count-able,
 * non-body fields (module, anchor_type, status, reason code). Never logs body
 * text, query, chunk text, or case id.
 */
export function logHighlightNavigation({
  relatedModule,
  result,
  logger = defaultLogger,
}: {
  relatedModule: HighlightRelatedModule;
  result: HighlightNavigationResult;
  logger?: (payload: HighlightNavigationLog) => void;
}) {
  const payload: HighlightNavigationLog = {
    event: "source_highlight_navigation",
    related_module: relatedModule,
    anchor_type: sanitizeAnchorType(result.anchorType),
    status: result.status,
    reason_code:
      result.status === "degraded"
        ? result.reason || FALLBACK_DEGRADE_REASON
        : null,
    count: 1,
  };

  try {
    logger(payload);
  } catch {
    // logging must never break the reading flow
  }
}

export type HighlightNavigationLog = {
  event: "source_highlight_navigation";
  related_module: HighlightRelatedModule;
  anchor_type: string | null;
  status: HighlightNavigationStatus;
  reason_code: HighlightDegradeReason | null;
  count: number;
};

function sanitizeAnchorType(anchorType?: string): string | null {
  const normalized = (anchorType || "").trim();
  if (!normalized) {
    return null;
  }
  // anchor_type is a controlled vocabulary; keep only short slug-like tokens.
  return /^[a-z0-9_]{1,32}$/i.test(normalized) ? normalized : null;
}

function defaultResolveTarget(elementId: string): ScrollableTarget | null {
  if (typeof document === "undefined") {
    return null;
  }
  return document.getElementById(elementId);
}

function defaultLogger(payload: HighlightNavigationLog) {
  if (typeof console === "undefined" || typeof console.info !== "function") {
    return;
  }
  console.info(JSON.stringify(payload));
}
