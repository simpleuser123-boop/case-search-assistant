import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  DRAFTING_DRAFTS_API_PATH,
  createDraft,
  listDrafts,
  getDraft,
  updateDraft,
  toDraftRequestBody,
  hasCandidateAnchor,
  hasStatuteAnchor,
  STRUCTURE_SKELETON_ITEM_MAX_LEN,
  STRUCTURE_SKELETON_MAX_ITEMS,
  type DraftDraftInput,
} from "./draftingApi";

// create/update 请求体白名单（只允许这 5 个键）。
const BODY_ALLOWED_KEYS = [
  "structure_skeleton",
  "candidate_refs",
  "statute_refs",
  "note",
  "tag",
].sort();

// CandidateRef 请求体白名单七字段。
const CANDIDATE_ALLOWED_KEYS = [
  "case_id",
  "case_number",
  "court",
  "trial_level",
  "case_cause",
  "judgment_date",
  "source_anchors",
].sort();

// StatuteRef 请求体白名单字段。
const STATUTE_ALLOWED_KEYS = [
  "statute_id",
  "law_name",
  "article_no",
  "statute_anchors",
  "article_text",
  "source_corpus",
  "effective_status",
  "related_case_refs",
].sort();

const ANCHORED_CANDIDATE = {
  case_id: "case_001",
  case_number: "(2021)京01民终123号",
  court: "北京一中院",
  trial_level: "二审",
  case_cause: "买卖合同纠纷",
  judgment_date: "2021-06-01",
  source_anchors: [{ case_id: "case_001", source_chunk_id: "chunk_7", anchor_type: "holding" }],
};

const ANCHORED_STATUTE = {
  statute_id: "statute_刑法_266",
  law_name: "中华人民共和国刑法",
  article_no: "第二百六十六条",
  statute_anchors: [{ text_id: "law_刑法_266_0", law_name: "中华人民共和国刑法" }],
  article_text: "诈骗公私财物……（来自语料的条文）",
  source_corpus: "JuDGE law_corpus",
  effective_status: "现行有效",
  related_case_refs: [],
};

const VALID_INPUT: DraftDraftInput = {
  structure_skeleton: ["一、基本案情", "二、争议焦点", "三、参考类案"],
  candidate_refs: [ANCHORED_CANDIDATE],
  statute_refs: [ANCHORED_STATUTE],
  note: "本草稿用于内部讨论",
  tag: "买卖合同",
};

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("toDraftRequestBody whitelist (no spread)", () => {
  it("emits only the 5 whitelisted top-level keys", () => {
    const body = toDraftRequestBody(VALID_INPUT);
    expect(Object.keys(body).sort()).toEqual(BODY_ALLOWED_KEYS);
  });

  it("drops injected draft body / judgment body / win-probability / extra keys", () => {
    const polluted = {
      ...VALID_INPUT,
      draft_body: "本院认为，被告应承担全部责任……（起草正文，禁止）",
      generated_text: "AI 生成的段落正文",
      conclusion: "原告胜诉",
      win_probability: 0.87,
      verdict: "胜诉",
      raw_case: "原告张三，电话13800138000",
      password: "secret",
    } as unknown as DraftDraftInput;
    const body = toDraftRequestBody(polluted);
    expect(Object.keys(body).sort()).toEqual(BODY_ALLOWED_KEYS);
    const serialized = JSON.stringify(body);
    expect(serialized).not.toContain("draft_body");
    expect(serialized).not.toContain("generated_text");
    expect(serialized).not.toContain("conclusion");
    expect(serialized).not.toContain("win_probability");
    expect(serialized).not.toContain("胜诉");
    expect(serialized).not.toContain("13800138000");
    expect(serialized).not.toContain("secret");
  });

  it("structure_skeleton emits only title strings, trims blanks, clamps item length and count", () => {
    const longTitle = "标".repeat(STRUCTURE_SKELETON_ITEM_MAX_LEN + 30);
    const tooMany = Array.from({ length: STRUCTURE_SKELETON_MAX_ITEMS + 10 }, (_, i) => `标题${i}`);
    const body = toDraftRequestBody({
      structure_skeleton: ["  一、基本案情  ", "", "   ", longTitle, ...tooMany],
      candidate_refs: [],
      statute_refs: [],
    });
    const skeleton = body.structure_skeleton as string[];
    expect(Array.isArray(skeleton)).toBe(true);
    // 全部为字符串标题，无空项。
    expect(skeleton.every((s) => typeof s === "string" && s.trim().length > 0)).toBe(true);
    // 首项已去空白。
    expect(skeleton[0]).toBe("一、基本案情");
    // 单项不超过上限。
    expect(skeleton.every((s) => s.length <= STRUCTURE_SKELETON_ITEM_MAX_LEN)).toBe(true);
    // 总项数不超过上限。
    expect(skeleton.length).toBeLessThanOrEqual(STRUCTURE_SKELETON_MAX_ITEMS);
  });

  it("candidate_refs emit only the 7 whitelisted keys + anchors, never body keys", () => {
    const pollutedRef = {
      ...ANCHORED_CANDIDATE,
      summary: "案件摘要正文",
      highlight: "高亮正文",
      chunk_text: "裁判文书 chunk 正文",
      judgment_text: "判决全文",
    } as unknown as typeof ANCHORED_CANDIDATE;
    const body = toDraftRequestBody({
      structure_skeleton: ["一、基本案情"],
      candidate_refs: [pollutedRef],
      statute_refs: [],
    });
    const refs = body.candidate_refs as Record<string, unknown>[];
    expect(refs).toHaveLength(1);
    expect(Object.keys(refs[0]).sort()).toEqual(CANDIDATE_ALLOWED_KEYS);
    const serialized = JSON.stringify(refs[0]);
    expect(serialized).not.toContain("summary");
    expect(serialized).not.toContain("chunk_text");
    expect(serialized).not.toContain("judgment_text");
    expect(serialized).not.toContain("判决全文");
  });

  it("statute_refs emit only whitelisted keys + anchors", () => {
    const body = toDraftRequestBody({
      structure_skeleton: ["一、法律依据"],
      candidate_refs: [],
      statute_refs: [ANCHORED_STATUTE],
    });
    const refs = body.statute_refs as Record<string, unknown>[];
    expect(refs).toHaveLength(1);
    expect(Object.keys(refs[0]).sort()).toEqual(STATUTE_ALLOWED_KEYS);
  });

  it("drops refs that lack an anchor (no anchor -> not in body)", () => {
    const noAnchorCandidate = { ...ANCHORED_CANDIDATE, source_anchors: [] };
    const noAnchorStatute = { ...ANCHORED_STATUTE, statute_anchors: [] };
    const body = toDraftRequestBody({
      structure_skeleton: ["一、基本案情"],
      candidate_refs: [ANCHORED_CANDIDATE, noAnchorCandidate],
      statute_refs: [ANCHORED_STATUTE, noAnchorStatute],
    });
    expect((body.candidate_refs as unknown[]).length).toBe(1);
    expect((body.statute_refs as unknown[]).length).toBe(1);
  });

  it("clamps note/tag and nulls blanks", () => {
    const body = toDraftRequestBody({
      structure_skeleton: ["一、基本案情"],
      candidate_refs: [],
      statute_refs: [],
      note: "   ",
      tag: "  买卖合同  ",
    });
    expect(body.note).toBeNull();
    expect(body.tag).toBe("买卖合同");
  });
});

describe("anchor guards", () => {
  it("hasCandidateAnchor requires case_id + source_chunk_id", () => {
    expect(hasCandidateAnchor(ANCHORED_CANDIDATE)).toBe(true);
    expect(hasCandidateAnchor({ ...ANCHORED_CANDIDATE, source_anchors: [] })).toBe(false);
    expect(
      hasCandidateAnchor({
        ...ANCHORED_CANDIDATE,
        source_anchors: [{ case_id: "c", source_chunk_id: "" }],
      }),
    ).toBe(false);
  });

  it("hasStatuteAnchor requires a non-empty text_id", () => {
    expect(hasStatuteAnchor(ANCHORED_STATUTE)).toBe(true);
    expect(hasStatuteAnchor({ ...ANCHORED_STATUTE, statute_anchors: [] })).toBe(false);
  });
});

describe("createDraft / updateDraft / listDrafts / getDraft fetch wiring", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "AbortController",
      class {
        signal = {} as AbortSignal;
        abort() {}
      },
    );
  });

  it("createDraft POSTs a whitelist-only JSON body to the drafts endpoint", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        draft_id: "draft_1",
        structure_skeleton: ["一、基本案情"],
        candidate_refs: [],
        statute_refs: [],
        note: null,
        tag: null,
        owner_user_id: "u1",
        team_id: null,
        visibility: "private",
        status: "active",
      }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const result = await createDraft(VALID_INPUT, { token: "tok_abc" });
    expect(result.ok).toBe(true);

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe(DRAFTING_DRAFTS_API_PATH);
    expect((init as RequestInit).method).toBe("POST");
    const sentBody = JSON.parse(String((init as RequestInit).body));
    expect(Object.keys(sentBody).sort()).toEqual(BODY_ALLOWED_KEYS);
    // 鉴权用 Authorization Bearer，不把 token 放进 body / URL。
    expect((init as RequestInit).headers).toMatchObject({ Authorization: "Bearer tok_abc" });
    expect(url).not.toContain("tok_abc");
  });

  it("maps 403 to disabled and 401 to login_required", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: false,
      status: 403,
      json: async () => ({ error: { code: "DRAFTING_DISABLED" } }),
    });
    vi.stubGlobal("fetch", fetchMock);
    const disabled = await createDraft(VALID_INPUT);
    expect(disabled).toMatchObject({ ok: false, reason: "disabled", reasonCode: "DRAFTING_DISABLED" });

    const fetchMock401 = vi.fn().mockResolvedValue({
      ok: false,
      status: 401,
      json: async () => ({ error: { code: "DRAFTING_REQUIRES_LOGIN" } }),
    });
    vi.stubGlobal("fetch", fetchMock401);
    const login = await listDrafts();
    expect(login).toMatchObject({ ok: false, reason: "login_required" });
  });

  it("updateDraft PUTs to /drafts/{id} with whitelist-only body and draft id in path not query", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        draft_id: "draft_42",
        structure_skeleton: ["一、基本案情"],
        candidate_refs: [],
        statute_refs: [],
        owner_user_id: "u1",
        visibility: "private",
        status: "active",
      }),
    });
    vi.stubGlobal("fetch", fetchMock);

    await updateDraft("draft_42", VALID_INPUT);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe(`${DRAFTING_DRAFTS_API_PATH}/draft_42`);
    expect(url).not.toContain("?");
    expect((init as RequestInit).method).toBe("PUT");
    const sentBody = JSON.parse(String((init as RequestInit).body));
    expect(Object.keys(sentBody).sort()).toEqual(BODY_ALLOWED_KEYS);
  });

  it("getDraft GETs /drafts/{id} without a body", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        draft_id: "draft_7",
        structure_skeleton: ["一、基本案情"],
        candidate_refs: [],
        statute_refs: [],
        owner_user_id: "u1",
        visibility: "private",
        status: "active",
      }),
    });
    vi.stubGlobal("fetch", fetchMock);

    await getDraft("draft_7");
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe(`${DRAFTING_DRAFTS_API_PATH}/draft_7`);
    expect((init as RequestInit).method).toBe("GET");
    expect((init as RequestInit).body).toBeUndefined();
  });
});
