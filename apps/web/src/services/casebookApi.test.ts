import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
// E7-4 casebook 共享 API 客户端测试（含 toCaseFolderShareBody / shareCaseFolder）。

import {
  CASEBOOK_FOLDERS_API_PATH,
  createCaseFolder,
  listCaseFolders,
  getCaseFolder,
  updateCaseFolder,
  shareCaseFolder,
  toCaseFolderCreateBody,
  toCaseFolderUpdateBody,
  toCaseFolderShareBody,
  hasCandidateAnchor,
  hasStatuteAnchor,
  TITLE_MAX_LEN,
  type CaseFolderInput,
} from "./casebookApi";

// create 请求体白名单（只允许这 6 个键，create 永不发 visibility）。
const CREATE_BODY_ALLOWED_KEYS = [
  "search_profile_summary",
  "candidate_refs",
  "draft_descriptors",
  "title",
  "note",
  "tag",
].sort();

// search_profile_summary 脱敏白名单子集 5 键。
const SUMMARY_ALLOWED_KEYS = [
  "case_cause",
  "region",
  "trial_level_preference",
  "dispute_focus_keywords",
  "query_text",
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

// DraftDescriptor 请求体白名单字段。
const DRAFT_ALLOWED_KEYS = [
  "draft_id",
  "structure_skeleton",
  "candidate_refs",
  "statute_refs",
  "note",
  "tag",
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

const ANCHORED_DRAFT = {
  draft_id: "draft_1",
  structure_skeleton: ["一、基本案情", "二、争议焦点"],
  candidate_refs: [ANCHORED_CANDIDATE],
  statute_refs: [],
  note: "内部讨论",
  tag: "买卖合同",
};

const VALID_INPUT: CaseFolderInput = {
  search_profile_summary: {
    case_cause: "买卖合同纠纷",
    region: "北京市",
    trial_level_preference: "二审",
    dispute_focus_keywords: ["质量", "违约"],
    query_text: "买卖合同 货款 违约",
  },
  candidate_refs: [ANCHORED_CANDIDATE],
  draft_descriptors: [ANCHORED_DRAFT],
  title: "买卖合同类案协作夹",
  note: "本协作夹用于内部讨论",
  tag: "买卖合同",
};

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("toCaseFolderCreateBody whitelist (no spread)", () => {
  it("emits only the 6 whitelisted top-level keys (create never sends visibility)", () => {
    const body = toCaseFolderCreateBody(VALID_INPUT);
    expect(Object.keys(body).sort()).toEqual(CREATE_BODY_ALLOWED_KEYS);
    expect(Object.keys(body)).not.toContain("visibility");
  });

  it("drops injected case summary / judgment body / draft body / win-probability / raw case / extra keys", () => {
    const polluted = {
      ...VALID_INPUT,
      case_overview: "AI 生成的案件综述（禁止）",
      generated_summary: "归纳结论正文",
      conclusion: "原告胜诉",
      win_probability: 0.87,
      verdict: "胜诉",
      judgment_text: "本院认为……（裁判正文）",
      raw_case: "原告张三，电话13800138000",
      password: "secret",
      visibility: "team",
    } as unknown as CaseFolderInput;
    const body = toCaseFolderCreateBody(polluted);
    expect(Object.keys(body).sort()).toEqual(CREATE_BODY_ALLOWED_KEYS);
    const serialized = JSON.stringify(body);
    expect(serialized).not.toContain("case_overview");
    expect(serialized).not.toContain("generated_summary");
    expect(serialized).not.toContain("conclusion");
    expect(serialized).not.toContain("win_probability");
    expect(serialized).not.toContain("胜诉");
    expect(serialized).not.toContain("judgment_text");
    expect(serialized).not.toContain("13800138000");
    expect(serialized).not.toContain("secret");
  });

  it("search_profile_summary emits only the 5 desensitized subset keys, never raw case body", () => {
    const polluted = {
      ...VALID_INPUT,
      search_profile_summary: {
        case_cause: "买卖合同纠纷",
        region: "北京市",
        trial_level_preference: "二审",
        dispute_focus_keywords: ["质量"],
        query_text: "买卖合同",
        raw_narrative: "原告张三于2021年向被告……（原始口语化案情，禁止）",
        defendant_name: "李四",
        phone: "13800138000",
      } as unknown as CaseFolderInput["search_profile_summary"],
    };
    const body = toCaseFolderCreateBody(polluted);
    const summary = body.search_profile_summary as Record<string, unknown>;
    expect(Object.keys(summary).sort()).toEqual(SUMMARY_ALLOWED_KEYS);
    const serialized = JSON.stringify(summary);
    expect(serialized).not.toContain("raw_narrative");
    expect(serialized).not.toContain("原始口语化案情");
    expect(serialized).not.toContain("defendant_name");
    expect(serialized).not.toContain("李四");
    expect(serialized).not.toContain("13800138000");
  });

  it("nulls empty search_profile_summary instead of sending an empty shell", () => {
    const body = toCaseFolderCreateBody({
      ...VALID_INPUT,
      search_profile_summary: { case_cause: null, region: "  " } as unknown as CaseFolderInput["search_profile_summary"],
    });
    expect(body.search_profile_summary).toBeNull();
  });

  it("candidate_refs emit only the 7 whitelisted keys + anchors, never body keys", () => {
    const pollutedRef = {
      ...ANCHORED_CANDIDATE,
      summary: "案件摘要正文",
      highlight: "高亮正文",
      chunk_text: "裁判文书 chunk 正文",
      judgment_text: "判决全文",
    } as unknown as typeof ANCHORED_CANDIDATE;
    const body = toCaseFolderCreateBody({
      ...VALID_INPUT,
      candidate_refs: [pollutedRef],
      draft_descriptors: [],
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

  it("draft_descriptors emit only whitelisted keys (skeleton titles only, no body)", () => {
    const pollutedDraft = {
      ...ANCHORED_DRAFT,
      draft_body: "起草正文（禁止）",
      generated_text: "AI 生成段落正文",
    } as unknown as typeof ANCHORED_DRAFT;
    const body = toCaseFolderCreateBody({
      ...VALID_INPUT,
      candidate_refs: [],
      draft_descriptors: [pollutedDraft],
    });
    const drafts = body.draft_descriptors as Record<string, unknown>[];
    expect(drafts).toHaveLength(1);
    expect(Object.keys(drafts[0]).sort()).toEqual(DRAFT_ALLOWED_KEYS);
    const serialized = JSON.stringify(drafts[0]);
    expect(serialized).not.toContain("draft_body");
    expect(serialized).not.toContain("generated_text");
    expect(serialized).not.toContain("起草正文");
  });

  it("drops candidate refs that lack an anchor (no anchor -> not in body)", () => {
    const noAnchorCandidate = { ...ANCHORED_CANDIDATE, source_anchors: [] };
    const body = toCaseFolderCreateBody({
      ...VALID_INPUT,
      candidate_refs: [ANCHORED_CANDIDATE, noAnchorCandidate],
      draft_descriptors: [],
    });
    expect((body.candidate_refs as unknown[]).length).toBe(1);
  });

  it("drops draft descriptors that lack any skeleton title", () => {
    const noSkeleton = { ...ANCHORED_DRAFT, structure_skeleton: ["  ", ""] };
    const body = toCaseFolderCreateBody({
      ...VALID_INPUT,
      candidate_refs: [],
      draft_descriptors: [ANCHORED_DRAFT, noSkeleton],
    });
    expect((body.draft_descriptors as unknown[]).length).toBe(1);
  });

  it("clamps title/note/tag and nulls blanks", () => {
    const longTitle = "标".repeat(TITLE_MAX_LEN + 30);
    const body = toCaseFolderCreateBody({
      candidate_refs: [],
      draft_descriptors: [],
      title: longTitle,
      note: "   ",
      tag: "  买卖合同  ",
    });
    expect((body.title as string).length).toBe(TITLE_MAX_LEN);
    expect(body.note).toBeNull();
    expect(body.tag).toBe("买卖合同");
  });
});

describe("toCaseFolderUpdateBody", () => {
  it("matches create body plus optional visibility when explicitly provided", () => {
    const body = toCaseFolderUpdateBody({ ...VALID_INPUT, visibility: "team" });
    expect(Object.keys(body).sort()).toEqual([...CREATE_BODY_ALLOWED_KEYS, "visibility"].sort());
    expect(body.visibility).toBe("team");
  });

  it("omits visibility when not provided (no implicit sharing)", () => {
    const body = toCaseFolderUpdateBody(VALID_INPUT);
    expect(Object.keys(body)).not.toContain("visibility");
  });
});

describe("toCaseFolderShareBody (E7-4, visibility metadata only)", () => {
  it("emits visibility + team_id for share-to-team (only these keys, no body/refs)", () => {
    const body = toCaseFolderShareBody({ visibility: "team", team_id: "  team_7 " });
    expect(Object.keys(body).sort()).toEqual(["team_id", "visibility"]);
    expect(body.visibility).toBe("team");
    expect(body.team_id).toBe("team_7");
  });

  it("emits only visibility for unshare (private never sends team_id)", () => {
    const body = toCaseFolderShareBody({ visibility: "private", team_id: "team_7" });
    expect(Object.keys(body)).toEqual(["visibility"]);
    expect(body.visibility).toBe("private");
  });

  it("drops blank team_id when sharing to team (no empty team_id leaks)", () => {
    const body = toCaseFolderShareBody({ visibility: "team", team_id: "   " });
    expect(Object.keys(body)).toEqual(["visibility"]);
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
    const statute = {
      statute_id: "s1",
      law_name: "中华人民共和国刑法",
      statute_anchors: [{ text_id: "law_刑法_266_0" }],
    };
    expect(hasStatuteAnchor(statute)).toBe(true);
    expect(hasStatuteAnchor({ ...statute, statute_anchors: [] })).toBe(false);
  });
});

describe("create / list / get / update / share fetch wiring", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "AbortController",
      class {
        signal = {} as AbortSignal;
        abort() {}
      },
    );
  });

  it("createCaseFolder POSTs a whitelist-only JSON body to the folders endpoint", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        case_folder_id: "folder_1",
        owner_user_id: "u1",
        team_id: null,
        visibility: "private",
        candidate_refs: [],
        draft_descriptors: [],
        status: "active",
      }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const result = await createCaseFolder(VALID_INPUT, { token: "tok_abc" });
    expect(result.ok).toBe(true);

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe(CASEBOOK_FOLDERS_API_PATH);
    expect((init as RequestInit).method).toBe("POST");
    const sentBody = JSON.parse(String((init as RequestInit).body));
    expect(Object.keys(sentBody).sort()).toEqual(CREATE_BODY_ALLOWED_KEYS);
    // 鉴权用 Authorization Bearer，不把 token 放进 body / URL。
    expect((init as RequestInit).headers).toMatchObject({ Authorization: "Bearer tok_abc" });
    expect(url).not.toContain("tok_abc");
  });

  it("maps 403 to disabled and 401 to login_required", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: false,
      status: 403,
      json: async () => ({ error: { code: "CASEBOOK_DISABLED" } }),
    });
    vi.stubGlobal("fetch", fetchMock);
    const disabled = await createCaseFolder(VALID_INPUT);
    expect(disabled).toMatchObject({ ok: false, reason: "disabled", reasonCode: "CASEBOOK_DISABLED" });

    const fetchMock401 = vi.fn().mockResolvedValue({
      ok: false,
      status: 401,
      json: async () => ({ error: { code: "CASEBOOK_REQUIRES_LOGIN" } }),
    });
    vi.stubGlobal("fetch", fetchMock401);
    const login = await listCaseFolders();
    expect(login).toMatchObject({ ok: false, reason: "login_required" });
  });

  it("listCaseFolders sends X-Team-Id header when teamId given", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ folders: [], folder_count: 0 }),
    });
    vi.stubGlobal("fetch", fetchMock);
    await listCaseFolders({ teamId: "team_9" });
    const [, init] = fetchMock.mock.calls[0];
    expect((init as RequestInit).headers).toMatchObject({ "X-Team-Id": "team_9" });
  });

  it("updateCaseFolder PUTs to /folders/{id} with whitelist body and id in path not query", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        case_folder_id: "folder_42",
        owner_user_id: "u1",
        visibility: "private",
        candidate_refs: [],
        draft_descriptors: [],
        status: "active",
      }),
    });
    vi.stubGlobal("fetch", fetchMock);

    await updateCaseFolder("folder_42", VALID_INPUT);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe(`${CASEBOOK_FOLDERS_API_PATH}/folder_42`);
    expect(url).not.toContain("?");
    expect((init as RequestInit).method).toBe("PUT");
    const sentBody = JSON.parse(String((init as RequestInit).body));
    // 未显式给 visibility 时不发（无隐式共享）。
    expect(Object.keys(sentBody).sort()).toEqual(CREATE_BODY_ALLOWED_KEYS);
  });

  it("getCaseFolder GETs /folders/{id} without a body", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        case_folder_id: "folder_7",
        owner_user_id: "u1",
        visibility: "private",
        candidate_refs: [],
        draft_descriptors: [],
        status: "active",
      }),
    });
    vi.stubGlobal("fetch", fetchMock);

    await getCaseFolder("folder_7");
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe(`${CASEBOOK_FOLDERS_API_PATH}/folder_7`);
    expect((init as RequestInit).method).toBe("GET");
    expect((init as RequestInit).body).toBeUndefined();
  });

  it("shareCaseFolder POSTs to /folders/{id}/share with id in path not query", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        case_folder_id: "folder_5",
        owner_user_id: "u1",
        team_id: "team_7",
        visibility: "team",
        candidate_refs: [],
        draft_descriptors: [],
        status: "active",
      }),
    });
    vi.stubGlobal("fetch", fetchMock);
    const result = await shareCaseFolder("folder_5", { visibility: "team", team_id: "team_7" });
    expect(result.ok).toBe(true);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe(`${CASEBOOK_FOLDERS_API_PATH}/folder_5/share`);
    expect(url).not.toContain("?");
    expect((init as RequestInit).method).toBe("POST");
    const sentBody = JSON.parse(String((init as RequestInit).body));
    expect(Object.keys(sentBody).sort()).toEqual(["team_id", "visibility"]);
  });
});
