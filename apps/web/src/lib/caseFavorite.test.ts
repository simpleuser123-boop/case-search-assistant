import { beforeEach, describe, expect, it } from "vitest";

import {
  addFavorite,
  buildFavoriteLog,
  buildFavoriteRecord,
  CASE_FAVORITE_STORAGE_KEY,
  clearFavorites,
  FAVORITE_NOTE_MAX_CHARS,
  FAVORITE_TAG_MAX_CHARS,
  isFavorited,
  loadFavorites,
  MAX_FAVORITE_ENTRIES,
  removeFavorite,
  sanitizeFavoriteAnchors,
  toggleFavorite,
  truncateShortField,
  updateFavoriteFields,
  type FavoriteMetadataSource,
  type StorageLike,
} from "./caseFavorite";

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

// A realistic on-screen metadata source. The forbidden body-ish fields below
// (summary/matched_text/judgment text) must NEVER end up in the stored record.
const resultSource: FavoriteMetadataSource & Record<string, unknown> = {
  case_id: "case-uuid-001",
  case_no: "（2021）京0105民初12345号",
  court: "北京市朝阳区人民法院",
  trial_level: "一审",
  case_cause: "买卖合同纠纷",
  judgment_date: "2021-08-12",
  source_anchors: [
    { case_id: "case-uuid-001", source_chunk_id: "chunk-3", anchor_type: "case_record", chunk_type: "fact" },
    // cross-case anchor must be filtered out
    { case_id: "other-case", source_chunk_id: "chunk-9", anchor_type: "case_record" },
    // missing source_chunk_id must be dropped
    { case_id: "case-uuid-001", source_chunk_id: "", anchor_type: "case_record" },
  ],
  // The following are NOT part of FavoriteMetadataSource and must be ignored:
  summary: { text: "裁判要旨正文……（不应被收藏）" },
  matched_text: "本院查明：被告多次延期交货……（正文，不应被收藏）",
  judgment_long_text: "判决如下……（裁判文书正文，不应被收藏）",
};

let storage: ReturnType<typeof memoryStorage>;

beforeEach(() => {
  storage = memoryStorage();
});

describe("caseFavorite short-field truncation", () => {
  it("collapses whitespace and caps note/tag length", () => {
    const longNote = "甲".repeat(FAVORITE_NOTE_MAX_CHARS + 50);
    expect(Array.from(truncateShortField(longNote, FAVORITE_NOTE_MAX_CHARS)).length).toBe(
      FAVORITE_NOTE_MAX_CHARS
    );
    expect(truncateShortField("  多  空   格 ", FAVORITE_TAG_MAX_CHARS)).toBe("多 空 格");
    expect(truncateShortField(undefined, FAVORITE_TAG_MAX_CHARS)).toBe("");
  });
});

describe("caseFavorite anchor sanitization", () => {
  it("keeps only same-case anchors with both case_id and source_chunk_id", () => {
    const anchors = sanitizeFavoriteAnchors(resultSource, "case-uuid-001");
    expect(anchors).toHaveLength(1);
    expect(anchors[0]).toMatchObject({
      case_id: "case-uuid-001",
      source_chunk_id: "chunk-3",
      anchor_type: "case_record",
    });
  });
});

describe("caseFavorite record building (metadata only)", () => {
  it("builds a record from on-screen metadata and never copies body text", () => {
    const record = buildFavoriteRecord(resultSource, { note: "重点参考", tag: "买卖" });
    expect(record).toMatchObject({
      case_id: "case-uuid-001",
      case_number: "（2021）京0105民初12345号",
      court: "北京市朝阳区人民法院",
      trial_level: "一审",
      case_cause: "买卖合同纠纷",
      judgment_date: "2021-08-12",
      note: "重点参考",
      tag: "买卖",
      favorite_status: "favorited",
    });
    // Whitelist guard: serialized record must not contain any body-ish content.
    const serialized = JSON.stringify(record);
    expect(serialized).not.toContain("裁判要旨");
    expect(serialized).not.toContain("本院查明");
    expect(serialized).not.toContain("判决如下");
    // Only whitelisted keys are present.
    expect(Object.keys(record).sort()).toEqual(
      [
        "case_cause",
        "case_id",
        "case_number",
        "court",
        "created_at",
        "favorite_status",
        "judgment_date",
        "note",
        "source_anchors",
        "tag",
        "trial_level",
      ].sort()
    );
  });

  it("falls back to court_level when trial_level is blank", () => {
    const record = buildFavoriteRecord({ case_id: "c1", court_level: "二审" });
    expect(record.trial_level).toBe("二审");
  });
});

describe("caseFavorite CRUD round-trip", () => {
  it("adds, detects, persists, and removes a favorite", () => {
    const added = addFavorite(storage, [], resultSource);
    expect(added.added).toBe(true);
    expect(added.entries).toHaveLength(1);
    expect(isFavorited(added.entries, "case-uuid-001")).toBe(true);

    // Persisted JSON in storage must not carry body text.
    const stored = storage.dump()[CASE_FAVORITE_STORAGE_KEY];
    expect(stored).toBeTruthy();
    expect(stored).not.toContain("本院查明");
    expect(stored).not.toContain("裁判要旨");

    const reloaded = loadFavorites(storage);
    expect(reloaded).toHaveLength(1);
    expect(reloaded[0].case_id).toBe("case-uuid-001");

    const afterRemove = removeFavorite(storage, reloaded, "case-uuid-001");
    expect(afterRemove).toHaveLength(0);
    expect(isFavorited(afterRemove, "case-uuid-001")).toBe(false);
  });

  it("is idempotent: a second add of the same case_id does not duplicate", () => {
    const first = addFavorite(storage, [], resultSource);
    const second = addFavorite(storage, first.entries, resultSource);
    expect(second.added).toBe(false);
    expect(second.reason).toBe("already_favorited");
    expect(second.entries).toHaveLength(1);
  });

  it("rejects a source without case_id", () => {
    const result = addFavorite(storage, [], { case_no: "无id" });
    expect(result.added).toBe(false);
    expect(result.reason).toBe("missing_case_id");
    expect(result.entries).toHaveLength(0);
  });

  it("toggles favorite on and off", () => {
    const on = toggleFavorite(storage, [], resultSource);
    expect(on.favorited).toBe(true);
    const off = toggleFavorite(storage, on.entries, resultSource);
    expect(off.favorited).toBe(false);
    expect(off.entries).toHaveLength(0);
  });

  it("clears all favorites", () => {
    const added = addFavorite(storage, [], resultSource).entries;
    expect(added).toHaveLength(1);
    clearFavorites(storage);
    expect(loadFavorites(storage)).toHaveLength(0);
  });
});

describe("caseFavorite user fields", () => {
  it("updates and truncates note/tag without touching other records", () => {
    const base = addFavorite(storage, [], resultSource).entries;
    const longNote = "笔".repeat(FAVORITE_NOTE_MAX_CHARS + 20);
    const updated = updateFavoriteFields(storage, base, "case-uuid-001", {
      note: longNote,
      tag: "标签超长".repeat(20),
    });
    expect(Array.from(updated[0].note).length).toBe(FAVORITE_NOTE_MAX_CHARS);
    expect(Array.from(updated[0].tag).length).toBe(FAVORITE_TAG_MAX_CHARS);
  });
});

describe("caseFavorite resilience & sanitation on load", () => {
  it("returns [] for corrupt JSON and degrades on throwing storage", () => {
    storage.setItem(CASE_FAVORITE_STORAGE_KEY, "{not json");
    expect(loadFavorites(storage)).toEqual([]);
    expect(loadFavorites(throwingStorage)).toEqual([]);
    expect(addFavorite(throwingStorage, [], resultSource).added).toBe(true); // memory state still returned
  });

  it("drops records missing case_id and strips non-whitelisted keys on load", () => {
    const poisoned = JSON.stringify([
      {
        case_id: "c-keep",
        case_number: "案号",
        // attacker/legacy body fields that must be dropped on load:
        matched_text: "本院查明正文",
        chunk_body: "chunk 正文",
        judgment_long_text: "裁判文书正文",
      },
      { case_number: "无 case_id，应丢弃" },
    ]);
    storage.setItem(CASE_FAVORITE_STORAGE_KEY, poisoned);
    const loaded = loadFavorites(storage);
    expect(loaded).toHaveLength(1);
    expect(loaded[0].case_id).toBe("c-keep");
    const keys = Object.keys(loaded[0]);
    expect(keys).not.toContain("matched_text");
    expect(keys).not.toContain("chunk_body");
    expect(keys).not.toContain("judgment_long_text");
    expect(JSON.stringify(loaded[0])).not.toContain("正文");
  });

  it("dedupes by case_id on load (keeps first occurrence)", () => {
    const dupes = JSON.stringify([
      { case_id: "dup", case_number: "A", created_at: "2026-06-13T10:00:00.000Z" },
      { case_id: "dup", case_number: "B", created_at: "2026-06-13T09:00:00.000Z" },
    ]);
    storage.setItem(CASE_FAVORITE_STORAGE_KEY, dupes);
    expect(loadFavorites(storage)).toHaveLength(1);
  });

  it("caps stored favorites at MAX_FAVORITE_ENTRIES", () => {
    const many = Array.from({ length: MAX_FAVORITE_ENTRIES + 10 }, (_, i) => ({
      case_id: `c-${i}`,
      case_number: `案号-${i}`,
      created_at: new Date(Date.now() - i * 1000).toISOString(),
    }));
    storage.setItem(CASE_FAVORITE_STORAGE_KEY, JSON.stringify(many));
    expect(loadFavorites(storage).length).toBe(MAX_FAVORITE_ENTRIES);
  });
});

describe("caseFavorite sanitized logging", () => {
  it("emits only event/surface/status/reason_code/count — no body, case number, note, or tag", () => {
    const log = buildFavoriteLog({ surface: "result_card", status: "favorited" });
    expect(log).toEqual({
      event: "case_favorite_action",
      surface: "result_card",
      status: "favorited",
      reason_code: null,
      count: 1,
    });
    const serialized = JSON.stringify(log);
    expect(serialized).not.toContain("案号");
    expect(serialized).not.toContain("case-uuid-001");
  });
});
