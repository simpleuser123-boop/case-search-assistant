import { describe, expect, it, vi } from "vitest";

import {
  HIGHLIGHT_DEGRADE_REASONS,
  logHighlightNavigation,
  navigateToSourceChunk,
  resolveHighlightDisplay,
  sourceChunkElementId,
  type HighlightNavigationLog,
} from "./sourceHighlights";

describe("sourceHighlights navigation helper", () => {
  it("navigates when the target source chunk element exists", () => {
    const scrollIntoView = vi.fn();
    const result = navigateToSourceChunk({
      chunkId: "case-1-c2",
      anchorType: "detail_chunk",
      resolveTarget: () => ({ scrollIntoView }),
    });

    expect(result.status).toBe("navigated");
    expect(result.reason).toBeUndefined();
    expect(scrollIntoView).toHaveBeenCalledOnce();
    expect(scrollIntoView).toHaveBeenCalledWith({
      block: "start",
      behavior: "smooth",
    });
  });

  it("degrades with missing_source_anchor when chunk id is blank", () => {
    const result = navigateToSourceChunk({ chunkId: "  " });
    expect(result.status).toBe("degraded");
    expect(result.reason).toBe("missing_source_anchor");
  });

  it("degrades with highlight_target_missing when the element is absent", () => {
    const result = navigateToSourceChunk({
      chunkId: "case-1-c9",
      resolveTarget: () => null,
    });
    expect(result.status).toBe("degraded");
    expect(result.reason).toBe("highlight_target_missing");
  });

  it("degrades with navigation_failed when scrollIntoView throws", () => {
    const result = navigateToSourceChunk({
      chunkId: "case-1-c2",
      resolveTarget: () => ({
        scrollIntoView: () => {
          throw new Error("boom");
        },
      }),
    });
    expect(result.status).toBe("degraded");
    expect(result.reason).toBe("navigation_failed");
  });

  it("degrades with navigation_failed when target resolution throws", () => {
    const result = navigateToSourceChunk({
      chunkId: "case-1-c2",
      resolveTarget: () => {
        throw new Error("dom blew up");
      },
    });
    expect(result.status).toBe("degraded");
    expect(result.reason).toBe("navigation_failed");
  });

  it("builds a stable, encoded source chunk element id", () => {
    expect(sourceChunkElementId("case 1/c2")).toBe(
      "source-chunk-case%201%2Fc2"
    );
  });
});

describe("resolveHighlightDisplay", () => {
  const navigable = new Set(["case-1-c1", "case-1-c2"]);

  it("is available when anchor is complete and chunk is navigable", () => {
    const display = resolveHighlightDisplay({
      anchor: { case_id: "case-1", source_chunk_id: "case-1-c1" },
      navigableChunkIds: navigable,
    });
    expect(display.displayStatus).toBe("available");
    expect(display.degradeReason).toBeUndefined();
  });

  it("degrades when anchor is missing required ids", () => {
    const display = resolveHighlightDisplay({
      anchor: { case_id: "", source_chunk_id: "" },
      navigableChunkIds: navigable,
    });
    expect(display.displayStatus).toBe("degraded");
    expect(display.degradeReason).toBe("missing_source_anchor");
  });

  it("degrades when the chunk is not navigable", () => {
    const display = resolveHighlightDisplay({
      anchor: { case_id: "case-1", source_chunk_id: "case-1-c99" },
      navigableChunkIds: navigable,
    });
    expect(display.displayStatus).toBe("degraded");
    expect(display.degradeReason).toBe("source_chunk_unavailable");
  });
});

describe("logHighlightNavigation sanitization", () => {
  it("logs only count-able, non-body fields and a known reason code", () => {
    const logged: HighlightNavigationLog[] = [];
    logHighlightNavigation({
      relatedModule: "issue_focus",
      result: { status: "degraded", reason: "highlight_target_missing", anchorType: "detail_chunk" },
      logger: (payload) => logged.push(payload),
    });

    expect(logged).toHaveLength(1);
    const entry = logged[0];
    expect(entry).toEqual({
      event: "source_highlight_navigation",
      related_module: "issue_focus",
      anchor_type: "detail_chunk",
      status: "degraded",
      reason_code: "highlight_target_missing",
      count: 1,
    });
    expect(HIGHLIGHT_DEGRADE_REASONS).toContain(entry.reason_code);
    // No body-like keys ever present.
    const keys = Object.keys(entry);
    ["text", "chunk_text", "query", "case_id", "label", "body"].forEach((k) =>
      expect(keys).not.toContain(k)
    );
  });

  it("drops an anchor_type that is not a controlled slug", () => {
    const logged: HighlightNavigationLog[] = [];
    logHighlightNavigation({
      relatedModule: "holding_summary",
      result: {
        status: "navigated",
        anchorType: "原始裁判文书正文片段，禁止写入日志",
      },
      logger: (payload) => logged.push(payload),
    });
    expect(logged[0].anchor_type).toBeNull();
    expect(logged[0].reason_code).toBeNull();
  });

  it("never throws even if the logger throws", () => {
    expect(() =>
      logHighlightNavigation({
        relatedModule: "key_elements",
        result: { status: "navigated" },
        logger: () => {
          throw new Error("logger down");
        },
      })
    ).not.toThrow();
  });
});
