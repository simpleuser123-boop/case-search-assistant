import { describe, expect, it, vi } from "vitest";

import {
  buildCitationCopyRecord,
  buildCitationFromDetail,
  buildCitationFromResult,
  buildCitationCopyLog,
  CITATION_COPY_REASON_CODES,
  copyCitation,
  formatCitation,
  logCitationCopy,
  resolveCopyText,
  type CitationCopyLog,
} from "./citationCopy";
import type { CaseDetailResponse, SearchResultItem } from "../types/search";

function makeResult(overrides: Partial<SearchResultItem> = {}): SearchResultItem {
  return {
    case_id: "case-1",
    source_chunk_ids: [],
    hit_chunk_ids: [],
    retrieval_source: [],
    score_breakdown: {},
    highlights: [],
    metadata: {},
    case_no: "（2021）京01民终1234号",
    court: "北京市第一中级人民法院",
    trial_level: "二审",
    judgment_date: "2021-06-01",
    ...overrides,
  } as SearchResultItem;
}

describe("buildCitationCopyRecord", () => {
  it("collects metadata-only fields and assembles a citation format", () => {
    const record = buildCitationFromResult(makeResult());
    expect(record).toMatchObject({
      case_id: "case-1",
      case_number: "（2021）京01民终1234号",
      court: "北京市第一中级人民法院",
      trial_level: "二审",
      judgment_date: "2021-06-01",
      copy_status: "idle",
    });
    expect(record.citation_format).toBe(
      "北京市第一中级人民法院 （2021）京01民终1234号 （二审） 2021-06-01"
    );
    // structure carries no body/summary/highlight fields
    expect(Object.keys(record).sort()).toEqual(
      [
        "case_id",
        "case_number",
        "citation_format",
        "copy_status",
        "court",
        "judgment_date",
        "trial_level",
      ].sort()
    );
  });

  it("falls back to court_level when trial_level is absent", () => {
    const record = buildCitationCopyRecord({
      case_id: "c2",
      case_no: "案号X",
      court_level: "中级",
    });
    expect(record.trial_level).toBe("中级");
  });

  it("drops blank fields from the citation line", () => {
    const record = buildCitationCopyRecord({ case_no: "仅有案号号" });
    expect(record.citation_format).toBe("仅有案号号");
  });

  it("prefers detail fields but fills blanks from the seed", () => {
    const detail = {
      case_id: "case-9",
      case_no: "",
      court: "广州互联网法院",
      trial_level: "",
      court_level: "",
      judgment_date: "2022-02-02",
      chunks: [],
      degraded: false,
      degraded_reasons: [],
      timings: {
        rewrite_duration_ms: 0,
        embedding_duration_ms: 0,
        retrieval_duration_ms: 0,
        rerank_duration_ms: 0,
        summary_duration_ms: 0,
        total_duration_ms: 0,
      },
    } as CaseDetailResponse;
    const record = buildCitationFromDetail(detail, makeResult({ case_id: "case-9" }));
    expect(record.case_number).toBe("（2021）京01民终1234号"); // from seed
    expect(record.court).toBe("广州互联网法院"); // from detail
    expect(record.judgment_date).toBe("2022-02-02"); // from detail
  });
});

describe("formatCitation", () => {
  it("joins present fields in 法院 案号 审级 日期 order", () => {
    expect(
      formatCitation({
        caseNumber: "案号A",
        court: "某法院",
        trialLevel: "一审",
        judgmentDate: "2020-01-01",
      })
    ).toBe("某法院 案号A （一审） 2020-01-01");
  });

  it("returns empty string when nothing is present", () => {
    expect(
      formatCitation({ caseNumber: "", court: "", trialLevel: "", judgmentDate: "" })
    ).toBe("");
  });
});

describe("resolveCopyText", () => {
  it("returns the case number for kind=case_number", () => {
    const record = buildCitationFromResult(makeResult());
    expect(resolveCopyText(record, "case_number")).toEqual({
      text: "（2021）京01民终1234号",
    });
  });

  it("degrades with missing_case_number when there is no case number", () => {
    const record = buildCitationFromResult(makeResult({ case_no: "" }));
    expect(resolveCopyText(record, "case_number")).toEqual({
      reason: "missing_case_number",
    });
  });

  it("degrades with missing_metadata when citation is empty", () => {
    const record = buildCitationCopyRecord({});
    expect(resolveCopyText(record, "citation")).toEqual({
      reason: "missing_metadata",
    });
  });
});

describe("copyCitation", () => {
  it("writes the resolved text and returns copied", async () => {
    const writer = vi.fn().mockResolvedValue(undefined);
    const record = buildCitationFromResult(makeResult());
    const outcome = await copyCitation({ record, kind: "citation", writer });
    expect(outcome).toEqual({ status: "copied" });
    expect(writer).toHaveBeenCalledWith(record.citation_format);
  });

  it("returns unavailable (no write) when metadata is missing", async () => {
    const writer = vi.fn();
    const record = buildCitationFromResult(makeResult({ case_no: "" }));
    const outcome = await copyCitation({ record, kind: "case_number", writer });
    expect(outcome).toEqual({ status: "unavailable", reason: "missing_case_number" });
    expect(writer).not.toHaveBeenCalled();
  });

  it("returns failed with clipboard_write_failed when the writer throws", async () => {
    const writer = vi.fn().mockRejectedValue(new Error("denied"));
    const record = buildCitationFromResult(makeResult());
    const outcome = await copyCitation({ record, kind: "case_number", writer });
    expect(outcome).toEqual({ status: "failed", reason: "clipboard_write_failed" });
  });
});

describe("logCitationCopy sanitization", () => {
  it("logs only event/surface/kind/status/reason_code/count — never body text", () => {
    const logged: CitationCopyLog[] = [];
    logCitationCopy({
      surface: "detail",
      kind: "citation",
      outcome: { status: "copied" },
      logger: (payload) => logged.push(payload),
    });
    expect(logged).toHaveLength(1);
    expect(logged[0]).toEqual({
      event: "citation_copy_action",
      surface: "detail",
      kind: "citation",
      status: "copied",
      reason_code: null,
      count: 1,
    });
    const keys = Object.keys(logged[0]);
    ["text", "citation_format", "case_number", "case_id", "court", "query", "body"].forEach(
      (k) => expect(keys).not.toContain(k)
    );
  });

  it("uses a known reason code on degraded outcomes", () => {
    const log = buildCitationCopyLog({
      surface: "result_card",
      kind: "case_number",
      outcome: { status: "unavailable", reason: "clipboard_unavailable" },
    });
    expect(log.reason_code).toBe("clipboard_unavailable");
    expect(CITATION_COPY_REASON_CODES).toContain(log.reason_code!);
  });

  it("never throws even if the logger throws", () => {
    expect(() =>
      logCitationCopy({
        surface: "compare",
        kind: "citation",
        outcome: { status: "copied" },
        logger: () => {
          throw new Error("logger down");
        },
      })
    ).not.toThrow();
  });
});
