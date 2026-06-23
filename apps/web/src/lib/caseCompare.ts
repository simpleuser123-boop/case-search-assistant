import type {
  CaseDetailResponse,
  FactAlignmentResponse,
  HoldingSummary,
  ReadingNavigationSection,
  RiskHint,
  SearchResultItem,
  SourceAnchor,
} from "../types/search";

// M3-6 case comparison (受控横向对比).
//
// This layer assembles a *read-only* comparison structure from data that has
// already been fetched for the current search session (case detail + lazy fact
// alignment + risk hints from the current result responses). It is pure: it
// never fetches, never persists, never mutates ranking, and never derives a
// legal conclusion. Every case-side AI-processed cell must carry a source
// anchor or degrade with a sanitized reason code; cells without a verifiable
// anchor are NOT shown.
//
// Boundary (M3-6): this is an in-memory aid for the current reading judgment
// only. It is not a favorite / history / export / case list / report. Nothing
// here is written to storage, and selection is expected to live in ephemeral
// React state that resets on every new search.

export const MAX_COMPARE_CASES = 3;
export const MIN_COMPARE_CASES = 2;

export type CompareSectionKey =
  | "metadata"
  | "holding_summary"
  | "issue_focus"
  | "fact_dimension"
  | "risk_hints";

export type CompareCellStatus = "available" | "degraded" | "loading";

export type CompareDegradeReason =
  | "detail_unavailable"
  | "detail_loading"
  | "module_degraded"
  | "missing_source_anchor"
  | "source_chunk_unavailable"
  | "no_anchored_content"
  | "no_flagged_risk";

export const COMPARE_DEGRADE_REASONS: readonly CompareDegradeReason[] = [
  "detail_unavailable",
  "detail_loading",
  "module_degraded",
  "missing_source_anchor",
  "source_chunk_unavailable",
  "no_anchored_content",
  "no_flagged_risk",
];

// A single piece of anchored, displayable content inside one comparison cell.
export type CompareEntry = {
  // Short label / classification when the source module provides one.
  label?: string;
  // Anchored text excerpt. Only present when an anchor is verified.
  text?: string;
  // The verified source anchor backing this entry (case_id + source_chunk_id).
  anchor: SourceAnchor;
  // Optional secondary anchors (e.g. multi-chunk holding items).
  extraAnchors?: SourceAnchor[];
};

// One case's column for one section.
export type CompareCell = {
  caseId: string;
  sectionKey: CompareSectionKey;
  status: CompareCellStatus;
  entries: CompareEntry[];
  degradeReason?: CompareDegradeReason;
};

export type CompareSection = {
  key: CompareSectionKey;
  title: string;
  // module_status: per-case display status for this section.
  moduleStatus: Record<string, CompareCellStatus>;
  cells: CompareCell[];
};

// The case_compare data structure (per M3-6 task #1).
export type CaseCompare = {
  selectedCaseIds: string[];
  compareSections: CompareSection[];
  // source_anchors: flattened, de-duplicated list of every anchor actually
  // rendered in the comparison, grouped per case. Guarantees the "每个案例侧
  // 对比单元必须有来源锚点或明确降级" invariant is inspectable.
  sourceAnchors: Record<string, SourceAnchor[]>;
  // module_status: per-section, per-case status matrix.
  moduleStatus: Record<CompareSectionKey, Record<string, CompareCellStatus>>;
  // degrade_reason: per-section, per-case reason code (only when degraded).
  degradeReason: Record<CompareSectionKey, Record<string, CompareDegradeReason>>;
};

export const COMPARE_SECTION_TITLES: Record<CompareSectionKey, string> = {
  metadata: "元数据",
  holding_summary: "裁判要旨摘要",
  issue_focus: "争议焦点与关键要素",
  fact_dimension: "事实维度",
  risk_hints: "风险提示与不利线索",
};

const COMPARE_SECTION_ORDER: CompareSectionKey[] = [
  "metadata",
  "holding_summary",
  "issue_focus",
  "fact_dimension",
  "risk_hints",
];

const MAX_ENTRIES_PER_CELL = 4;

// Per-case inputs already available in the current session. Detail / fact
// alignment may be loading or unavailable; the builder degrades safely.
export type CaseCompareSource = {
  caseId: string;
  seed?: SearchResultItem | null;
  detail?: CaseDetailResponse | null;
  detailLoading?: boolean;
  factAlignment?: FactAlignmentResponse | null;
  factAlignmentLoading?: boolean;
};

export function isUsableAnchor(
  anchor: SourceAnchor | null | undefined
): anchor is SourceAnchor {
  return Boolean(anchor?.case_id?.trim() && anchor?.source_chunk_id?.trim());
}

function navigableChunkIds(detail?: CaseDetailResponse | null): Set<string> {
  const ids = new Set<string>();
  for (const chunk of detail?.chunks || []) {
    const id = (chunk?.chunk_id || "").trim();
    if (id) {
      ids.add(id);
    }
  }
  return ids;
}

// Pick the first anchor that is both well-formed and points at a navigable
// chunk of THIS case. Cross-case anchors are filtered out so a cell can never
// borrow another case's source.
function resolveCellAnchor(
  caseId: string,
  anchors: SourceAnchor[] | undefined,
  navigable: Set<string>
): { anchor: SourceAnchor; extra: SourceAnchor[] } | { reason: CompareDegradeReason } {
  const usable = (anchors || []).filter(isUsableAnchor).filter(
    (anchor) => anchor.case_id === caseId
  );
  if (usable.length === 0) {
    return { reason: "missing_source_anchor" };
  }
  const navigableAnchors = usable.filter((anchor) =>
    navigable.has(anchor.source_chunk_id)
  );
  if (navigableAnchors.length === 0) {
    return { reason: "source_chunk_unavailable" };
  }
  const [first, ...rest] = navigableAnchors;
  return { anchor: first, extra: rest };
}

function clampText(text: string | null | undefined): string | undefined {
  const normalized = (text || "").trim();
  return normalized.length > 0 ? normalized : undefined;
}

function loadingCell(caseId: string, sectionKey: CompareSectionKey): CompareCell {
  return {
    caseId,
    sectionKey,
    status: "loading",
    entries: [],
    degradeReason: "detail_loading",
  };
}

function degradedCell(
  caseId: string,
  sectionKey: CompareSectionKey,
  reason: CompareDegradeReason
): CompareCell {
  return { caseId, sectionKey, status: "degraded", entries: [], degradeReason: reason };
}

function availableCell(
  caseId: string,
  sectionKey: CompareSectionKey,
  entries: CompareEntry[]
): CompareCell {
  return { caseId, sectionKey, status: "available", entries };
}

// --- Section 1: metadata -----------------------------------------------------
// Metadata is catalog data from the case record itself (not AI-inferred). It is
// anchored to the case record. We still degrade when the record is unavailable
// or carries no identifying fields.
function buildMetadataCell(source: CaseCompareSource): CompareCell {
  const sectionKey: CompareSectionKey = "metadata";
  const detail = source.detail;
  const seed = source.seed;
  // Metadata is catalog data; render from the seed record immediately and only
  // fall back to loading/unavailable when there is no record to show at all.
  if (!detail && !seed) {
    return source.detailLoading
      ? loadingCell(source.caseId, sectionKey)
      : degradedCell(source.caseId, sectionKey, "detail_unavailable");
  }

  const record = detail ?? seed;
  const fields: Array<[string, string | null | undefined]> = [
    ["案号", record?.case_no],
    ["法院", record?.court],
    ["审级", detail?.trial_level ?? seed?.trial_level ?? detail?.court_level ?? seed?.court_level],
    ["案由", record?.case_cause],
    ["裁判日期", record?.judgment_date],
  ];
  const entries: CompareEntry[] = [];
  const recordAnchor: SourceAnchor = {
    case_id: source.caseId,
    source_chunk_id: `${source.caseId}::case_record`,
    anchor_type: "case_record",
    source_url: detail?.source_url ?? seed?.source_url ?? null,
    source_ref: detail?.source_name ?? null,
  };
  for (const [label, value] of fields) {
    const text = clampText(value);
    if (text) {
      entries.push({ label, text, anchor: recordAnchor });
    }
  }
  if (entries.length === 0) {
    return degradedCell(source.caseId, sectionKey, "no_anchored_content");
  }
  return availableCell(source.caseId, sectionKey, entries.slice(0, MAX_ENTRIES_PER_CELL + 1));
}

// --- Section 2: holding summary ---------------------------------------------
function buildHoldingCell(source: CaseCompareSource): CompareCell {
  const sectionKey: CompareSectionKey = "holding_summary";
  const detail = source.detail;
  if (!detail) {
    return source.detailLoading
      ? loadingCell(source.caseId, sectionKey)
      : degradedCell(source.caseId, sectionKey, "detail_unavailable");
  }
  const holding: HoldingSummary | null | undefined = detail.holding_summary;
  if (!holding || holding.generation_status !== "generated") {
    return degradedCell(source.caseId, sectionKey, "module_degraded");
  }
  const navigable = navigableChunkIds(detail);
  const entries: CompareEntry[] = [];
  for (const item of holding.summary_items || []) {
    const text = clampText(item.text);
    if (!text) {
      continue;
    }
    const resolved = resolveCellAnchor(source.caseId, item.source_anchors, navigable);
    if ("anchor" in resolved) {
      entries.push({ text, anchor: resolved.anchor, extraAnchors: resolved.extra });
    }
    if (entries.length >= MAX_ENTRIES_PER_CELL) {
      break;
    }
  }
  if (entries.length === 0) {
    return degradedCell(source.caseId, sectionKey, "no_anchored_content");
  }
  return availableCell(source.caseId, sectionKey, entries);
}

// --- Section 3: issue focus + key elements ----------------------------------
function buildReadingNavEntries(
  caseId: string,
  section: ReadingNavigationSection | null | undefined,
  navigable: Set<string>,
  limit: number
): CompareEntry[] {
  if (!section || section.generation_status !== "generated") {
    return [];
  }
  const entries: CompareEntry[] = [];
  for (const item of section.items || []) {
    const text = clampText(item.label);
    if (!text) {
      continue;
    }
    const resolved = resolveCellAnchor(caseId, item.source_anchors, navigable);
    if ("anchor" in resolved) {
      entries.push({
        label: clampText(item.category),
        text,
        anchor: resolved.anchor,
        extraAnchors: resolved.extra,
      });
    }
    if (entries.length >= limit) {
      break;
    }
  }
  return entries;
}

function buildIssueFocusCell(source: CaseCompareSource): CompareCell {
  const sectionKey: CompareSectionKey = "issue_focus";
  const detail = source.detail;
  if (!detail) {
    return source.detailLoading
      ? loadingCell(source.caseId, sectionKey)
      : degradedCell(source.caseId, sectionKey, "detail_unavailable");
  }
  const navigable = navigableChunkIds(detail);
  const entries = [
    ...buildReadingNavEntries(source.caseId, detail.issue_focus, navigable, 3),
    ...buildReadingNavEntries(source.caseId, detail.key_elements, navigable, 3),
  ].slice(0, MAX_ENTRIES_PER_CELL);
  if (entries.length === 0) {
    const bothDegraded =
      detail.issue_focus?.generation_status !== "generated" &&
      detail.key_elements?.generation_status !== "generated";
    return degradedCell(
      source.caseId,
      sectionKey,
      bothDegraded ? "module_degraded" : "no_anchored_content"
    );
  }
  return availableCell(source.caseId, sectionKey, entries);
}

// --- Section 4: fact dimension ----------------------------------------------
function buildFactCell(source: CaseCompareSource): CompareCell {
  const sectionKey: CompareSectionKey = "fact_dimension";
  const detail = source.detail;
  if (!detail) {
    return source.detailLoading
      ? loadingCell(source.caseId, sectionKey)
      : degradedCell(source.caseId, sectionKey, "detail_unavailable");
  }
  const alignment = source.factAlignment;
  if (!alignment) {
    return source.factAlignmentLoading
      ? loadingCell(source.caseId, sectionKey)
      : degradedCell(source.caseId, sectionKey, "module_degraded");
  }
  if (alignment.generation_status !== "generated") {
    return degradedCell(source.caseId, sectionKey, "module_degraded");
  }
  const navigable = navigableChunkIds(detail);
  const entries: CompareEntry[] = [];
  for (const item of alignment.items || []) {
    const facts = (item.case_side_facts || []).map(clampText).filter(Boolean) as string[];
    if (facts.length === 0) {
      continue;
    }
    const resolved = resolveCellAnchor(source.caseId, item.source_anchors, navigable);
    if ("anchor" in resolved) {
      entries.push({
        label: clampText(item.dimension),
        text: facts.join("；"),
        anchor: resolved.anchor,
        extraAnchors: resolved.extra,
      });
    }
    if (entries.length >= MAX_ENTRIES_PER_CELL) {
      break;
    }
  }
  if (entries.length === 0) {
    return degradedCell(source.caseId, sectionKey, "no_anchored_content");
  }
  return availableCell(source.caseId, sectionKey, entries);
}

// --- Section 5: risk hints + adverse leads ----------------------------------
// Risk hints come pre-anchored from the search response. An empty result here
// is informative ("no flagged risk"), not a failure, but it is still surfaced
// as a degraded/empty cell so the column never silently goes blank.
function buildRiskCell(
  source: CaseCompareSource,
  riskHintsByCase: Map<string, RiskHint[]>
): CompareCell {
  const sectionKey: CompareSectionKey = "risk_hints";
  const navigable = navigableChunkIds(source.detail);
  const hints = riskHintsByCase.get(source.caseId) || [];
  const entries: CompareEntry[] = [];
  const seen = new Set<string>();
  for (const hint of hints) {
    const anchor = (hint.source_anchors || []).filter(isUsableAnchor).find(
      (candidate) => candidate.case_id === source.caseId
    );
    if (!anchor) {
      continue;
    }
    // If we have the detail loaded, require the chunk to be navigable; when
    // detail is not loaded we still trust the search-provided anchor.
    if (navigable.size > 0 && !navigable.has(anchor.source_chunk_id)) {
      continue;
    }
    const key = `${hint.risk_type}:${anchor.source_chunk_id}:${hint.reason_code}`;
    if (seen.has(key)) {
      continue;
    }
    seen.add(key);
    entries.push({ label: hint.risk_type, text: hint.reason_code, anchor });
    if (entries.length >= MAX_ENTRIES_PER_CELL) {
      break;
    }
  }
  if (entries.length === 0) {
    return degradedCell(source.caseId, sectionKey, "no_flagged_risk");
  }
  return availableCell(source.caseId, sectionKey, entries);
}

const SECTION_BUILDERS: Record<
  CompareSectionKey,
  (source: CaseCompareSource, riskHintsByCase: Map<string, RiskHint[]>) => CompareCell
> = {
  metadata: (source) => buildMetadataCell(source),
  holding_summary: (source) => buildHoldingCell(source),
  issue_focus: (source) => buildIssueFocusCell(source),
  fact_dimension: (source) => buildFactCell(source),
  risk_hints: (source, riskHintsByCase) => buildRiskCell(source, riskHintsByCase),
};

export function buildCaseCompare(
  sources: CaseCompareSource[],
  riskHints: RiskHint[] = []
): CaseCompare {
  const selectedCaseIds = sources.map((source) => source.caseId);
  const riskHintsByCase = new Map<string, RiskHint[]>();
  for (const hint of riskHints) {
    for (const anchor of hint.source_anchors || []) {
      if (!isUsableAnchor(anchor)) {
        continue;
      }
      const list = riskHintsByCase.get(anchor.case_id) || [];
      list.push(hint);
      riskHintsByCase.set(anchor.case_id, list);
    }
  }

  const compareSections: CompareSection[] = [];
  const moduleStatus = {} as Record<CompareSectionKey, Record<string, CompareCellStatus>>;
  const degradeReason = {} as Record<
    CompareSectionKey,
    Record<string, CompareDegradeReason>
  >;
  const sourceAnchors: Record<string, SourceAnchor[]> = {};
  const seenAnchorKeys: Record<string, Set<string>> = {};

  for (const caseId of selectedCaseIds) {
    sourceAnchors[caseId] = [];
    seenAnchorKeys[caseId] = new Set<string>();
  }

  for (const sectionKey of COMPARE_SECTION_ORDER) {
    const cells: CompareCell[] = [];
    const statusByCase: Record<string, CompareCellStatus> = {};
    const reasonByCase: Record<string, CompareDegradeReason> = {};

    for (const source of sources) {
      const cell = SECTION_BUILDERS[sectionKey](source, riskHintsByCase);
      cells.push(cell);
      statusByCase[source.caseId] = cell.status;
      if (cell.degradeReason) {
        reasonByCase[source.caseId] = cell.degradeReason;
      }
      // Collect rendered anchors for the per-case anchor inventory.
      for (const entry of cell.entries) {
        const all = [entry.anchor, ...(entry.extraAnchors || [])];
        for (const anchor of all) {
          const key = `${anchor.source_chunk_id}:${anchor.anchor_type}`;
          if (!seenAnchorKeys[source.caseId].has(key)) {
            seenAnchorKeys[source.caseId].add(key);
            sourceAnchors[source.caseId].push(anchor);
          }
        }
      }
    }

    compareSections.push({
      key: sectionKey,
      title: COMPARE_SECTION_TITLES[sectionKey],
      moduleStatus: statusByCase,
      cells,
    });
    moduleStatus[sectionKey] = statusByCase;
    degradeReason[sectionKey] = reasonByCase;
  }

  return {
    selectedCaseIds,
    compareSections,
    sourceAnchors,
    moduleStatus,
    degradeReason,
  };
}

// Sanitized summary for logging / gate evidence. Contains ONLY counts, status,
// and reason codes — never body text, query, chunk text, case_no, or raw ids.
export type CaseCompareSummary = {
  selected_case_count: number;
  section_count: number;
  by_section: Record<
    CompareSectionKey,
    {
      available: number;
      degraded: number;
      loading: number;
      reason_codes: Record<string, number>;
    }
  >;
  cells_with_anchor: number;
  cells_total: number;
};

export function summarizeCaseCompare(compare: CaseCompare): CaseCompareSummary {
  const bySection = {} as CaseCompareSummary["by_section"];
  let cellsWithAnchor = 0;
  let cellsTotal = 0;

  for (const section of compare.compareSections) {
    const reasonCodes: Record<string, number> = {};
    let available = 0;
    let degraded = 0;
    let loading = 0;
    for (const cell of section.cells) {
      cellsTotal += 1;
      if (cell.status === "available") {
        available += 1;
        if (cell.entries.length > 0) {
          cellsWithAnchor += 1;
        }
      } else if (cell.status === "loading") {
        loading += 1;
      } else {
        degraded += 1;
      }
      if (cell.degradeReason) {
        reasonCodes[cell.degradeReason] = (reasonCodes[cell.degradeReason] || 0) + 1;
      }
    }
    bySection[section.key] = {
      available,
      degraded,
      loading,
      reason_codes: reasonCodes,
    };
  }

  return {
    selected_case_count: compare.selectedCaseIds.length,
    section_count: compare.compareSections.length,
    by_section: bySection,
    cells_with_anchor: cellsWithAnchor,
    cells_total: cellsTotal,
  };
}
