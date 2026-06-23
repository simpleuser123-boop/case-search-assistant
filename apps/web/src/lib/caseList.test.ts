import { beforeEach, describe, expect, it } from "vitest";

import {
  addItemToList,
  buildCaseListLog,
  buildListItem,
  CASE_LIST_STORAGE_KEY,
  clearLists,
  createList,
  deleteList,
  LIST_NOTE_MAX_CHARS,
  LIST_TAG_MAX_CHARS,
  LIST_TITLE_MAX_CHARS,
  listIdsContainingCase,
  loadLists,
  moveListItem,
  MAX_ITEMS_PER_LIST,
  removeItemFromList,
  renameList,
  reorderListItems,
  sanitizeListAnchors,
  truncateShortField,
  updateListItemFields,
  type ListItemMetadataSource,
  type StorageLike,
} from "./caseList";

function memoryStorage(): StorageLike & { dump(): Record<string, string> } {
  const map = new Map<string, string>();
  return {
    getItem: (k) => (map.has(k) ? map.get(k)! : null),
    setItem: (k, v) => void map.set(k, v),
    removeItem: (k) => void map.delete(k),
    dump: () => Object.fromEntries(map.entries()),
  };
}

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

// A metadata source that ALSO carries body-like fields. The builder must ignore them.
function sourceWithBody(overrides: Partial<ListItemMetadataSource> = {}): ListItemMetadataSource {
  return {
    case_id: "case-1",
    case_no: "（2021）京01民终123号",
    court: "北京市第一中级人民法院",
    trial_level: "二审",
    case_cause: "买卖合同纠纷",
    judgment_date: "2021-06-01",
    source_anchors: [
      { case_id: "case-1", source_chunk_id: "chunk-1", anchor_type: "case_record", chunk_type: "fact" },
      { case_id: "case-1", source_chunk_id: "chunk-2", anchor_type: "holding" },
      // cross-case anchor must be dropped
      { case_id: "other", source_chunk_id: "chunk-x", anchor_type: "fact" },
      // anchor missing chunk id must be dropped
      { case_id: "case-1", source_chunk_id: "", anchor_type: "fact" },
    ],
    ...overrides,
  };
}

const BODY_SENTINEL = "被告应于本判决生效之日起十日内向原告支付货款";

describe("buildListItem / sanitizeListAnchors", () => {
  it("builds an item from metadata only and never copies body text", () => {
    const dirty = {
      ...sourceWithBody(),
      // simulate body-ish keys leaking in from a result object
      case_fact_body: BODY_SENTINEL,
      chunk_body: BODY_SENTINEL,
      judgment_long_text: BODY_SENTINEL,
    } as unknown as ListItemMetadataSource;
    const item = buildListItem(dirty, { note: "重点参考", tag: "货款" });
    expect(item.case_id).toBe("case-1");
    expect(item.case_number).toBe("（2021）京01民终123号");
    expect(item.court).toBe("北京市第一中级人民法院");
    expect(item.note).toBe("重点参考");
    expect(item.tag).toBe("货款");
    // only whitelisted keys exist on the item
    expect(Object.keys(item).sort()).toEqual(
      [
        "added_at",
        "case_cause",
        "case_id",
        "case_number",
        "court",
        "judgment_date",
        "note",
        "source_anchors",
        "tag",
        "trial_level",
      ].sort()
    );
    // no body anywhere in the serialized item
    expect(JSON.stringify(item)).not.toContain(BODY_SENTINEL);
  });

  it("keeps only this-case anchors with both case_id and source_chunk_id", () => {
    const anchors = sanitizeListAnchors(sourceWithBody(), "case-1");
    expect(anchors).toHaveLength(2);
    expect(anchors.map((a) => a.source_chunk_id)).toEqual(["chunk-1", "chunk-2"]);
    expect(anchors.every((a) => a.case_id === "case-1")).toBe(true);
  });

  it("falls back case_number<-case_no and trial_level<-court_level", () => {
    const item = buildListItem({
      case_id: "c",
      case_number: "",
      case_no: "案号A",
      court_level: "基层",
    });
    expect(item.case_number).toBe("案号A");
    expect(item.trial_level).toBe("基层");
  });
});

describe("truncateShortField", () => {
  it("collapses whitespace and truncates to the cap", () => {
    expect(truncateShortField("  a   b  ", 40)).toBe("a b");
    expect(Array.from(truncateShortField("超".repeat(80), LIST_TITLE_MAX_CHARS)).length).toBe(
      LIST_TITLE_MAX_CHARS
    );
    expect(Array.from(truncateShortField("n".repeat(500), LIST_NOTE_MAX_CHARS)).length).toBe(
      LIST_NOTE_MAX_CHARS
    );
    expect(Array.from(truncateShortField("t".repeat(500), LIST_TAG_MAX_CHARS)).length).toBe(
      LIST_TAG_MAX_CHARS
    );
  });
});

describe("createList / addItemToList dedup", () => {
  let storage: ReturnType<typeof memoryStorage>;
  beforeEach(() => {
    storage = memoryStorage();
  });

  it("creates a list with an initial item", () => {
    const { lists, list, created } = createList(storage, [], "我的清单", sourceWithBody());
    expect(created).toBe(true);
    expect(list?.list_title).toBe("我的清单");
    expect(lists[0].items).toHaveLength(1);
    expect(lists[0].list_status).toBe("active");
  });

  it("dedupes the same case within one list", () => {
    let { lists } = createList(storage, [], "L", sourceWithBody());
    const listId = lists[0].list_id;
    const r1 = addItemToList(storage, lists, listId, sourceWithBody());
    expect(r1.changed).toBe(false);
    expect(r1.reason).toBe("already_in_list");
    expect(r1.lists[0].items).toHaveLength(1);
    // a different case adds fine
    const r2 = addItemToList(storage, lists, listId, sourceWithBody({ case_id: "case-2" }));
    expect(r2.changed).toBe(true);
    expect(r2.lists[0].items).toHaveLength(2);
  });

  it("rejects add to a missing list / missing case id", () => {
    const { lists } = createList(storage, [], "L");
    expect(addItemToList(storage, lists, "nope", sourceWithBody()).reason).toBe("list_not_found");
    expect(
      addItemToList(storage, lists, lists[0].list_id, { case_id: " " }).reason
    ).toBe("missing_case_id");
  });
});

describe("reorder / move (display-only, never affects ranking)", () => {
  let storage: ReturnType<typeof memoryStorage>;
  let listId: string;
  let lists: ReturnType<typeof createList>["lists"];
  beforeEach(() => {
    storage = memoryStorage();
    lists = createList(storage, [], "L", sourceWithBody({ case_id: "a" })).lists;
    listId = lists[0].list_id;
    lists = addItemToList(storage, lists, listId, sourceWithBody({ case_id: "b" })).lists;
    lists = addItemToList(storage, lists, listId, sourceWithBody({ case_id: "c" })).lists;
  });

  it("reorders to an explicit permutation", () => {
    const r = reorderListItems(storage, lists, listId, ["c", "a", "b"]);
    expect(r.changed).toBe(true);
    expect(r.lists[0].items.map((i) => i.case_id)).toEqual(["c", "a", "b"]);
  });

  it("rejects a non-permutation order", () => {
    expect(reorderListItems(storage, lists, listId, ["a", "b"]).reason).toBe("invalid_order");
    expect(reorderListItems(storage, lists, listId, ["a", "a", "b"]).reason).toBe("invalid_order");
    expect(reorderListItems(storage, lists, listId, ["a", "b", "z"]).reason).toBe("invalid_order");
  });

  it("moves an item up/down and clamps at the edges", () => {
    let r = moveListItem(storage, lists, listId, "b", "up");
    expect(r.lists[0].items.map((i) => i.case_id)).toEqual(["b", "a", "c"]);
    r = moveListItem(storage, r.lists, listId, "b", "up"); // already top
    expect(r.changed).toBe(false);
    expect(r.lists[0].items.map((i) => i.case_id)).toEqual(["b", "a", "c"]);
  });
});

describe("update / rename / remove / delete / clear", () => {
  let storage: ReturnType<typeof memoryStorage>;
  beforeEach(() => {
    storage = memoryStorage();
  });

  it("updates item note/tag with truncation", () => {
    let { lists } = createList(storage, [], "L", sourceWithBody());
    const listId = lists[0].list_id;
    const r = updateListItemFields(storage, lists, listId, "case-1", {
      note: "x".repeat(500),
      tag: "y".repeat(500),
    });
    expect(Array.from(r.lists[0].items[0].note).length).toBe(LIST_NOTE_MAX_CHARS);
    expect(Array.from(r.lists[0].items[0].tag).length).toBe(LIST_TAG_MAX_CHARS);
  });

  it("renames a list (short field) and removes items / deletes list", () => {
    let { lists } = createList(storage, [], "old", sourceWithBody());
    const listId = lists[0].list_id;
    lists = renameList(storage, lists, listId, "new").lists;
    expect(lists[0].list_title).toBe("new");
    lists = removeItemFromList(storage, lists, listId, "case-1").lists;
    expect(lists[0].items).toHaveLength(0);
    lists = deleteList(storage, lists, listId);
    expect(lists).toHaveLength(0);
  });

  it("clearLists wipes storage", () => {
    const { lists } = createList(storage, [], "L", sourceWithBody());
    expect(loadLists(storage)).toHaveLength(1);
    void lists;
    clearLists(storage);
    expect(loadLists(storage)).toHaveLength(0);
  });
});

describe("persistence sanitization (defense against tampered/body data)", () => {
  it("drops non-whitelist keys (incl. body) on load", () => {
    const storage = memoryStorage();
    const tampered = [
      {
        list_id: "L1",
        list_title: "t",
        created_at: "2026-01-01T00:00:00.000Z",
        updated_at: "2026-01-01T00:00:00.000Z",
        list_status: "active",
        evil_field: BODY_SENTINEL,
        items: [
          {
            case_id: "c1",
            case_number: "n",
            court: "court",
            trial_level: "一审",
            case_cause: "cause",
            judgment_date: "2020-01-01",
            note: "ok",
            tag: "t",
            added_at: "2026-01-01T00:00:00.000Z",
            source_anchors: [{ case_id: "c1", source_chunk_id: "ch1", anchor_type: "case_record" }],
            chunk_body: BODY_SENTINEL,
            judgment_long_text: BODY_SENTINEL,
          },
          { foo: "bar" }, // invalid item w/o case_id -> dropped
        ],
      },
      { nope: 1 }, // invalid list w/o list_id -> dropped
    ];
    storage.setItem(CASE_LIST_STORAGE_KEY, JSON.stringify(tampered));
    const loaded = loadLists(storage);
    expect(loaded).toHaveLength(1);
    expect(loaded[0].items).toHaveLength(1);
    const serialized = JSON.stringify(loaded);
    expect(serialized).not.toContain(BODY_SENTINEL);
    expect(serialized).not.toContain("evil_field");
    expect(serialized).not.toContain("chunk_body");
  });

  it("returns [] on corrupt JSON and degrades safely under throwing storage", () => {
    const storage = memoryStorage();
    storage.setItem(CASE_LIST_STORAGE_KEY, "{not json");
    expect(loadLists(storage)).toEqual([]);
    expect(loadLists(throwingStorage)).toEqual([]);
    // create under throwing storage must not throw; returns in-memory record
    expect(() => createList(throwingStorage, [], "L", sourceWithBody())).not.toThrow();
  });
});

describe("listIdsContainingCase (read-only reference relation)", () => {
  it("reports which lists contain a case without exposing ordering features", () => {
    const storage = memoryStorage();
    let lists = createList(storage, [], "A", sourceWithBody({ case_id: "x" })).lists;
    lists = createList(storage, lists, "B", sourceWithBody({ case_id: "y" })).lists;
    const idsForX = listIdsContainingCase(lists, "x");
    expect(idsForX).toHaveLength(1);
    expect(listIdsContainingCase(lists, "missing")).toEqual([]);
  });
});

describe("buildCaseListLog (desensitized telemetry)", () => {
  it("only emits event/surface/status/reason_code/count — no body/title/note", () => {
    const log = buildCaseListLog({ surface: "result_card", status: "item_added", count: 2 });
    expect(log).toEqual({
      event: "case_list_action",
      surface: "result_card",
      status: "item_added",
      reason_code: null,
      count: 2,
    });
    expect(Object.keys(log).sort()).toEqual(
      ["count", "event", "reason_code", "status", "surface"].sort()
    );
  });
});

describe("MAX_ITEMS_PER_LIST cap", () => {
  it("rejects items beyond the cap", () => {
    const storage = memoryStorage();
    let lists = createList(storage, [], "L").lists;
    const listId = lists[0].list_id;
    for (let i = 0; i < MAX_ITEMS_PER_LIST; i += 1) {
      lists = addItemToList(storage, lists, listId, sourceWithBody({ case_id: `c${i}` })).lists;
    }
    expect(lists[0].items).toHaveLength(MAX_ITEMS_PER_LIST);
    const over = addItemToList(storage, lists, listId, sourceWithBody({ case_id: "overflow" }));
    expect(over.changed).toBe(false);
    expect(over.reason).toBe("item_limit_reached");
  });
});
