import type { CaseDetailResponse, SearchResultItem } from "../types/search";

// M3-7 copy case number & basic citation format (复制案号与引用格式边界).
//
// This layer turns case *metadata* that is already on screen into a copyable
// citation string and drives the clipboard. It is intentionally tiny and
// strictly bounded:
//   - It only ever handles metadata (case number, court, trial level, judgment
//     date) and a basic citation format derived from those fields.
//   - It NEVER copies summaries, holding text, fact excerpts, highlighted
//     snippets, judgment body text, or any user input.
//   - It produces NO export file, NO similar-case list, NO report, and NO
//     analysis conclusion. There is no win/lose probability or legal verdict.
//   - It persists NOTHING: no copy history, no copied body, no cross-session
//     state. The only side effect is writing a short citation string to the
//     OS clipboard at the moment the user clicks.
//   - It does not touch ranking, recommendation, or compare selection.
//
// Everything here is pure / injectable so it can be unit-tested without a real
// browser clipboard.

// citation_copy data structure (per M3-7 task #1). Fields are metadata only.
export type CitationCopyStatus = "idle" | "copied" | "unavailable" | "failed";

export type CitationCopyRecord = {
  case_id: string;
  case_number: string;
  court: string;
  trial_level: string;
  judgment_date: string;
  // citation_format: the assembled, copyable string (metadata only).
  citation_format: string;
  // copy_status: lifecycle of the most recent copy attempt for this record.
  copy_status: CitationCopyStatus;
};

// What kind of copy the user asked for. case_number = just the docket number;
// citation = the basic assembled citation line. Both are metadata-only.
export type CitationCopyKind = "case_number" | "citation";

// Where the copy was triggered from — used only for sanitized telemetry.
export type CitationCopySurface = "result_card" | "detail" | "compare";

export type CitationCopyReasonCode =
  | "missing_case_number"
  | "missing_metadata"
  | "clipboard_unavailable"
  | "clipboard_write_failed";

// A lightweight, structural view of the metadata we are willing to copy. Both
// SearchResultItem and CaseDetailResponse satisfy it, so the same builder works
// from a result card, the detail drawer, or a compare column.
export type CitationMetadataSource = {
  case_id?: string | null;
  case_no?: string | null;
  court?: string | null;
  trial_level?: string | null;
  court_level?: string | null;
  judgment_date?: string | null;
};

function clean(value: string | null | undefined): string {
  return (value || "").trim();
}

// Build the metadata-only citation_copy record from anything that looks like a
// case metadata source. Reads trial_level then court_level as a fallback (the
// data model exposes both). No body text is ever read.
export function buildCitationCopyRecord(
  source: CitationMetadataSource | null | undefined
): CitationCopyRecord {
  const caseId = clean(source?.case_id);
  const caseNumber = clean(source?.case_no);
  const court = clean(source?.court);
  const trialLevel = clean(source?.trial_level) || clean(source?.court_level);
  const judgmentDate = clean(source?.judgment_date);

  return {
    case_id: caseId,
    case_number: caseNumber,
    court,
    trial_level: trialLevel,
    judgment_date: judgmentDate,
    citation_format: formatCitation({
      caseNumber,
      court,
      trialLevel,
      judgmentDate,
    }),
    copy_status: "idle",
  };
}

export function buildCitationFromResult(
  result: SearchResultItem
): CitationCopyRecord {
  return buildCitationCopyRecord(result);
}

export function buildCitationFromDetail(
  detail: CaseDetailResponse,
  seed?: CitationMetadataSource | null
): CitationCopyRecord {
  // Prefer detail fields; fall back to the seed result for any blank field so a
  // partially-degraded detail still yields the metadata that was on screen.
  return buildCitationCopyRecord({
    case_id: detail.case_id || seed?.case_id,
    case_no: detail.case_no || seed?.case_no,
    court: detail.court || seed?.court,
    trial_level:
      detail.trial_level || detail.court_level || seed?.trial_level || seed?.court_level,
    judgment_date: detail.judgment_date || seed?.judgment_date,
  });
}

// Assemble a basic, human-readable citation line from metadata fields only.
// Order: 法院 案号 （审级） 裁判日期. Blank fields are dropped so the line never
// shows placeholder noise. This is NOT a report — it is a single reference line.
export function formatCitation({
  caseNumber,
  court,
  trialLevel,
  judgmentDate,
}: {
  caseNumber: string;
  court: string;
  trialLevel: string;
  judgmentDate: string;
}): string {
  const parts: string[] = [];
  if (court) {
    parts.push(court);
  }
  if (caseNumber) {
    parts.push(caseNumber);
  }
  if (trialLevel) {
    parts.push(`（${trialLevel}）`);
  }
  if (judgmentDate) {
    parts.push(judgmentDate);
  }
  return parts.join(" ").trim();
}

// Resolve the exact text to place on the clipboard for a given copy kind.
// Returns null when the required metadata is absent (caller degrades safely).
export function resolveCopyText(
  record: CitationCopyRecord,
  kind: CitationCopyKind
): { text: string } | { reason: CitationCopyReasonCode } {
  if (kind === "case_number") {
    return record.case_number
      ? { text: record.case_number }
      : { reason: "missing_case_number" };
  }
  return record.citation_format
    ? { text: record.citation_format }
    : { reason: "missing_metadata" };
}

export type CitationCopyOutcome = {
  status: CitationCopyStatus;
  reason?: CitationCopyReasonCode;
};

type ClipboardWriter = (text: string) => Promise<void>;

// Default writer: prefer the async Clipboard API, fall back to nothing usable.
// We never throw to the caller; an unusable clipboard becomes a degraded
// outcome so the surrounding reading flow is unaffected.
function defaultClipboardWriter(text: string): Promise<void> {
  if (
    typeof navigator !== "undefined" &&
    navigator.clipboard &&
    typeof navigator.clipboard.writeText === "function"
  ) {
    return navigator.clipboard.writeText(text);
  }
  return Promise.reject(new Error("clipboard_unavailable"));
}

function clipboardAvailable(): boolean {
  return (
    typeof navigator !== "undefined" &&
    Boolean(navigator.clipboard) &&
    typeof navigator.clipboard.writeText === "function"
  );
}

// Perform a copy. Resolves the text for the requested kind, writes it to the
// clipboard, and returns a status + reason code. The copied text itself is
// never returned to telemetry — only the outcome.
export async function copyCitation({
  record,
  kind,
  writer = defaultClipboardWriter,
}: {
  record: CitationCopyRecord;
  kind: CitationCopyKind;
  writer?: ClipboardWriter;
}): Promise<CitationCopyOutcome> {
  const resolved = resolveCopyText(record, kind);
  if ("reason" in resolved) {
    return { status: "unavailable", reason: resolved.reason };
  }

  if (writer === defaultClipboardWriter && !clipboardAvailable()) {
    return { status: "unavailable", reason: "clipboard_unavailable" };
  }

  try {
    await writer(resolved.text);
    return { status: "copied" };
  } catch {
    return { status: "failed", reason: "clipboard_write_failed" };
  }
}

// citation_copy_action: sanitized, body-free telemetry. Records ONLY the event
// type, surface, kind, count, status, and reason code. It never records the
// copied text, case number, citation string, query, or any user input.
export type CitationCopyLog = {
  event: "citation_copy_action";
  surface: CitationCopySurface;
  kind: CitationCopyKind;
  status: CitationCopyStatus;
  reason_code: CitationCopyReasonCode | null;
  count: number;
};

export function buildCitationCopyLog({
  surface,
  kind,
  outcome,
  count = 1,
}: {
  surface: CitationCopySurface;
  kind: CitationCopyKind;
  outcome: CitationCopyOutcome;
  count?: number;
}): CitationCopyLog {
  return {
    event: "citation_copy_action",
    surface,
    kind,
    status: outcome.status,
    reason_code: outcome.reason ?? null,
    count: count > 0 ? Math.round(count) : 1,
  };
}

export function logCitationCopy({
  surface,
  kind,
  outcome,
  count = 1,
  logger = defaultLogger,
}: {
  surface: CitationCopySurface;
  kind: CitationCopyKind;
  outcome: CitationCopyOutcome;
  count?: number;
  logger?: (payload: CitationCopyLog) => void;
}): void {
  const payload = buildCitationCopyLog({ surface, kind, outcome, count });
  try {
    logger(payload);
  } catch {
    // logging must never break the reading flow
  }
}

function defaultLogger(payload: CitationCopyLog): void {
  if (typeof console === "undefined" || typeof console.info !== "function") {
    return;
  }
  console.info(JSON.stringify(payload));
}

export const CITATION_COPY_REASON_CODES: readonly CitationCopyReasonCode[] = [
  "missing_case_number",
  "missing_metadata",
  "clipboard_unavailable",
  "clipboard_write_failed",
];
