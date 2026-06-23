import { beforeEach, describe, expect, it } from "vitest";

import {
  appendHistory,
  clearDraft,
  clearHistory,
  HISTORY_PREVIEW_MAX_CHARS,
  loadDraft,
  loadHistory,
  MAX_HISTORY_ENTRIES,
  removeHistoryEntry,
  saveDraft,
  SEARCH_DRAFT_STORAGE_KEY,
  SEARCH_HISTORY_STORAGE_KEY,
  truncatePreview,
  type SearchHistoryEntry,
  type StorageLike,
} from "./searchHistory";

// In-memory StorageLike so the pure layer is testable without a real browser.
function memoryStorage(): StorageLike & { dump(): Record<string, string> } {
  const map = new Map<string, string>();
  return {
    getItem: (k) => (map.has(k) ? map.get(k)! : null),
    setItem: (k, v) => void map.set(k, v),
    removeItem: (k) => void map.delete(k),
    dump: () => Object.fromEntries(map.entries()),
  };
}

// Storage that throws on every op (private mode / quota) — must degrade safely.
const throwingStorage: StorageLike = {
  getItem: () => {
    throw new Error("blocked");
  },
  setItem: () => {
    throw new Error("blocked");
  },
  removeItem: () => {
    throw new Error("blocked");
  },
};

const rawCase =
  "买卖合同约定分批交付设备，买方已付款但卖方多次延期交货并拒绝退还预付款，双方就解除合同发生争议。";

let storage: ReturnType<typeof memoryStorage>;

beforeEach(() => {
  storage = memoryStorage();
});

describe("searchHistory draft", () => {
  it("saves, loads, and clears a draft round-trip", () => {
    expect(saveDraft(storage, rawCase)).toBe(true);
    const loaded = loadDraft(storage);
    expect(loaded?.draft_text).toBe(rawCase);
    expect(typeof loaded?.updated_at).toBe("string");

    clearDraft(storage);
    expect(loadDraft(storage)).toBeNull();
  });

  it("treats blank draft as a clear and never persists empty body", () => {
    saveDraft(storage, rawCase);
    expect(saveDraft(storage, "   ")).toBe(false);
    expect(storage.getItem(SEARCH_DRAFT_STORAGE_KEY)).toBeNull();
    expect(loadDraft(storage)).toBeNull();
  });

  it("returns null on corrupted draft json", () => {
    storage.setItem(SEARCH_DRAFT_STORAGE_KEY, "{not-json");
    expect(loadDraft(storage)).toBeNull();
  });

  it("degrades safely when storage throws", () => {
    expect(saveDraft(throwingStorage, rawCase)).toBe(false);
    expect(loadDraft(throwingStorage)).toBeNull();
    expect(() => clearDraft(throwingStorage)).not.toThrow();
  });
});

describe("searchHistory entries", () => {
  it("appends newest-first and stores a truncated preview", () => {
    let entries: SearchHistoryEntry[] = [];
    entries = appendHistory(storage, entries, {
      query_text: "第一个案情描述，关于产品责任纠纷的检索。",
      result_count: 3,
      degraded: false,
    });
    entries = appendHistory(storage, entries, {
      query_text: rawCase,
      result_count: 5,
      degraded: true,
    });

    expect(entries).toHaveLength(2);
    expect(entries[0].query_text).toBe(rawCase);
    expect(entries[0].result_count).toBe(5);
    expect(entries[0].degraded).toBe(true);
    expect(Array.from(entries[0].query_preview).length).toBeLessThanOrEqual(
      HISTORY_PREVIEW_MAX_CHARS + 1
    );
    // persisted to storage as well
    expect(loadHistory(storage)).toHaveLength(2);
  });

  it("dedupes identical query text and promotes it to the front", () => {
    let entries: SearchHistoryEntry[] = [];
    entries = appendHistory(storage, entries, {
      query_text: rawCase,
      result_count: 2,
      degraded: false,
    });
    entries = appendHistory(storage, entries, {
      query_text: "另一个不同的案情检索内容描述。",
      result_count: 4,
      degraded: false,
    });
    entries = appendHistory(storage, entries, {
      query_text: rawCase,
      result_count: 7,
      degraded: false,
    });

    expect(entries).toHaveLength(2);
    expect(entries[0].query_text).toBe(rawCase);
    expect(entries[0].result_count).toBe(7);
  });

  it("caps history at MAX_HISTORY_ENTRIES", () => {
    let entries: SearchHistoryEntry[] = [];
    for (let i = 0; i < MAX_HISTORY_ENTRIES + 5; i += 1) {
      entries = appendHistory(storage, entries, {
        query_text: `案情检索描述编号 ${i}，足够长以通过校验。`,
        result_count: i,
        degraded: false,
      });
    }
    expect(entries.length).toBe(MAX_HISTORY_ENTRIES);
    expect(loadHistory(storage).length).toBe(MAX_HISTORY_ENTRIES);
  });

  it("removes a single entry and clears all", () => {
    let entries: SearchHistoryEntry[] = [];
    entries = appendHistory(storage, entries, {
      query_text: rawCase,
      result_count: 2,
      degraded: false,
    });
    entries = appendHistory(storage, entries, {
      query_text: "第二条历史检索描述内容。",
      result_count: 1,
      degraded: false,
    });

    const targetId = entries[0].id;
    entries = removeHistoryEntry(storage, entries, targetId);
    expect(entries.find((e) => e.id === targetId)).toBeUndefined();
    expect(entries).toHaveLength(1);

    clearHistory(storage);
    expect(loadHistory(storage)).toHaveLength(0);
  });

  it("ignores blank query text on append", () => {
    const entries = appendHistory(storage, [], {
      query_text: "   ",
      result_count: 0,
      degraded: false,
    });
    expect(entries).toHaveLength(0);
  });

  it("returns empty array on corrupted or non-array history json", () => {
    storage.setItem(SEARCH_HISTORY_STORAGE_KEY, "{not-json");
    expect(loadHistory(storage)).toEqual([]);
    storage.setItem(SEARCH_HISTORY_STORAGE_KEY, JSON.stringify({ a: 1 }));
    expect(loadHistory(storage)).toEqual([]);
  });

  it("keeps storing the raw query_text locally for re-search (local-only by design)", () => {
    const entries = appendHistory(storage, [], {
      query_text: rawCase,
      result_count: 3,
      degraded: false,
    });
    // By contract the body lives ONLY in this local store. Assert it is here
    // (so re-search can refill it) and that the entry exposes it as query_text.
    expect(entries[0].query_text).toBe(rawCase);
    const persisted = storage.getItem(SEARCH_HISTORY_STORAGE_KEY) ?? "";
    expect(persisted).toContain(rawCase);
  });
});

describe("truncatePreview", () => {
  it("collapses whitespace and appends ellipsis past the limit", () => {
    const long = "甲".repeat(HISTORY_PREVIEW_MAX_CHARS + 10);
    const preview = truncatePreview(long);
    expect(preview.endsWith("…")).toBe(true);
    expect(Array.from(preview).length).toBe(HISTORY_PREVIEW_MAX_CHARS + 1);
  });

  it("leaves short text intact", () => {
    expect(truncatePreview("短文本")).toBe("短文本");
  });
});
