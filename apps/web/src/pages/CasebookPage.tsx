import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { isCasebookEnabled } from "../config/featureFlags";
import { getSession } from "../lib/sessionState";
import {
  createCaseFolder,
  listCaseFolders,
  updateCaseFolder,
  shareCaseFolder,
  hasCandidateAnchor,
  hasStatuteAnchor,
  TITLE_MAX_LEN,
  NOTE_MAX_LEN,
  TAG_MAX_LEN,
  type CasebookCandidateRefView,
  type CasebookDraftDescriptorView,
  type CasebookSearchProfileSummary,
  type CaseFolderView,
  type CaseFolderInput,
  type CaseFolderMutationResult,
  type CaseFolderListResult,
} from "../services/casebookApi";

// 案件协作工作台：用户把 SearchProfile 摘要（脱敏子集）、CandidateRef（类案，来自检索/清单）、
// DraftDescriptor（文书骨架，来自文书工作台）归集进 CaseFolder，自填 title/note/tag，调 E7-2
// 端点创建/读取/更新 CaseFolder（只归集、不起草、不下结论、不输出胜负）。
//
// 红线：
// - 协作夹态（摘要/引用/短字段）只存本组件 React state（内存），绝不写浏览器存储、绝不入 URL。
// - 引用只渲染元数据 + 锚点出处，绝不渲染裁判文书/候选/chunk/起草正文；无锚点引用前端拦截不可加入。
// - search_profile_summary 只取脱敏白名单子集；原始口语化案情绝不上送。
// - 本页**无任何** AI 生成案件综述 / 归纳结论 / 预测胜负的入口或调用——只搬运用户归集的引用与摘要。
// - E7-4 共享与协作权限：owner 在已保存协作夹上可显式切换「私有 / 团队共享」（private<->team），
//   非 owner 只读、无共享控件；共享只改 visibility 元数据，绝不放开正文/引用正文。

type SaveStatus =
  | { kind: "idle" }
  | { kind: "saving" }
  | { kind: "error"; message: string }
  | { kind: "saved" };

const DISABLED_REASON_TEXT: Record<string, string> = {
  CASEBOOK_DISABLED: "案件协作工作台当前未启用（后端 ENABLE_CASEBOOK=false）。",
};

export function CasebookPage() {
  // flag off：DOM 不渲染任何协作工作台入口/页面（安全末态，三重门控之第三重）。
  if (!isCasebookEnabled()) {
    return null;
  }
  return <CasebookWorkspace />;
}

function describeError(
  result: Extract<CaseFolderMutationResult | CaseFolderListResult, { ok: false }>,
): string {
  if (result.reason === "disabled") {
    return (
      (result.reasonCode && DISABLED_REASON_TEXT[result.reasonCode]) ||
      "案件协作工作台当前未启用，请稍后再试或联系管理员。"
    );
  }
  if (result.reason === "login_required") {
    return "案件协作工作台操作需先登录。";
  }
  if (result.reason === "timeout") {
    return "请求超时，请稍后重试。";
  }
  if (result.reason === "rejected") {
    return result.message || "归集内容未通过校验（引用须带锚点、摘要仅脱敏子集），请调整后重试。";
  }
  return "服务暂时不可用，请稍后重试。";
}

const VISIBILITY_LABEL: Record<string, string> = {
  private: "仅本人可见",
  team: "团队可见",
};

function CasebookWorkspace() {
  // 全部为内存 state：绝不入存储 / 不入 URL。
  const [summary, setSummary] = useState<CasebookSearchProfileSummary | null>(null);
  const [candidateRefs, setCandidateRefs] = useState<CasebookCandidateRefView[]>([]);
  const [draftDescriptors, setDraftDescriptors] = useState<CasebookDraftDescriptorView[]>([]);
  const [title, setTitle] = useState("");
  const [note, setNote] = useState("");
  const [tag, setTag] = useState("");

  const [editingFolderId, setEditingFolderId] = useState<string | null>(null);
  const [status, setStatus] = useState<SaveStatus>({ kind: "idle" });
  const [folders, setFolders] = useState<CaseFolderView[]>([]);
  const [listError, setListError] = useState<string | null>(null);
  // 团队态：带 X-Team-Id 列出后才能看到本人共享给团队的 visibility=team 协作夹（M5 隔离同款）。
  const [activeTeamId, setActiveTeamId] = useState("");
  const [shareError, setShareError] = useState<string | null>(null);

  // 当前登录用户 id：仅用于前端 owner-only 控件门控（后端仍权威校验 owner，越权 404）。
  const currentUserId = getSession()?.account.user_id ?? null;

  const refreshFolders = useCallback(async () => {
    const teamId = activeTeamId.trim();
    const result = await listCaseFolders(teamId ? { teamId } : {});
    if (result.ok) {
      setFolders(result.data.folders ?? []);
      setListError(null);
      return;
    }
    setFolders([]);
    setListError(describeError(result));
  }, [activeTeamId]);

  useEffect(() => {
    void refreshFolders();
  }, [refreshFolders]);

  async function handleShare(folder: CaseFolderView, visibility: "private" | "team", teamId: string) {
    setShareError(null);
    const result = await shareCaseFolder(folder.case_folder_id, {
      visibility,
      ...(visibility === "team" ? { team_id: teamId } : {}),
    });
    if (!result.ok) {
      setShareError(describeError(result));
      return;
    }
    // 共享到 team 后，切到对应团队态以便仍能看到该协作夹（私有则回单用户态）。
    if (visibility === "team") {
      setActiveTeamId(teamId);
    }
    void refreshFolders();
  }

  const trimmedTitle = title.trim();
  const hasContent =
    candidateRefs.length > 0 ||
    draftDescriptors.length > 0 ||
    summary != null ||
    trimmedTitle.length > 0;
  const canSave = hasContent && status.kind !== "saving";

  function resetForm() {
    setSummary(null);
    setCandidateRefs([]);
    setDraftDescriptors([]);
    setTitle("");
    setNote("");
    setTag("");
    setEditingFolderId(null);
    setStatus({ kind: "idle" });
  }

  async function handleSave() {
    if (!canSave) {
      return;
    }
    setStatus({ kind: "saving" });
    const input: CaseFolderInput = {
      search_profile_summary: summary,
      candidate_refs: candidateRefs,
      draft_descriptors: draftDescriptors,
      title,
      note,
      tag,
    };
    const result = editingFolderId
      ? await updateCaseFolder(editingFolderId, input)
      : await createCaseFolder(input);
    if (result.ok) {
      setStatus({ kind: "saved" });
      setEditingFolderId(result.data.case_folder_id);
      void refreshFolders();
      return;
    }
    setStatus({ kind: "error", message: describeError(result) });
  }

  function handleLoadFolder(folder: CaseFolderView) {
    // 把已保存协作夹载入编辑态（仍只是元数据 + 引用 + 短字段，零正文）。
    setSummary(folder.search_profile_summary ?? null);
    setCandidateRefs([...(folder.candidate_refs ?? [])]);
    setDraftDescriptors([...(folder.draft_descriptors ?? [])]);
    setTitle(folder.title ?? "");
    setNote(folder.note ?? "");
    setTag(folder.tag ?? "");
    setEditingFolderId(folder.case_folder_id);
    setStatus({ kind: "idle" });
  }

  return (
    <main
      aria-label="案件协作工作台"
      className="min-h-[100dvh] bg-[var(--color-bg)] px-4 py-6 text-[var(--color-text)] sm:px-6 sm:py-10"
    >
      <div className="mx-auto flex w-full max-w-[1180px] flex-col gap-8">
        <header className="flex items-start justify-between border-b border-[var(--color-border)] pb-4">
          <div>
            <p className="text-base font-semibold text-[var(--color-text)]">案件协作工作台</p>
            <p className="mt-1 text-xs text-[var(--color-text-muted)]">
              归集脱敏案情摘要 + 带来源锚点的类案/文书引用 + 短字段；只归集锚定来源，不起草、不归纳、不下结论
            </p>
          </div>
          <Link
            to="/"
            className="hidden rounded-[8px] border border-[var(--color-border-strong)] px-3 py-1.5 text-xs font-medium text-[var(--color-brand)] transition hover:bg-[var(--color-brand-soft)] sm:inline-flex"
          >
            返回检索助手
          </Link>
        </header>

        <SearchProfileSummaryEditor summary={summary} onChange={setSummary} />

        <CollectArea
          candidateRefs={candidateRefs}
          draftDescriptors={draftDescriptors}
          onAddCandidate={(ref) => setCandidateRefs((prev) => [...prev, ref])}
          onRemoveCandidate={(index) =>
            setCandidateRefs((prev) => prev.filter((_, i) => i !== index))
          }
          onAddDraft={(d) => setDraftDescriptors((prev) => [...prev, d])}
          onRemoveDraft={(index) =>
            setDraftDescriptors((prev) => prev.filter((_, i) => i !== index))
          }
        />

        <ShortFields
          title={title}
          onTitleChange={setTitle}
          note={note}
          onNoteChange={setNote}
          tag={tag}
          onTagChange={setTag}
        />

        <SaveBar
          editing={editingFolderId !== null}
          canSave={canSave}
          status={status}
          onSave={handleSave}
          onReset={resetForm}
        />

        <SavedFolders
          folders={folders}
          listError={listError}
          onLoad={handleLoadFolder}
          currentUserId={currentUserId}
          activeTeamId={activeTeamId}
          onActiveTeamIdChange={setActiveTeamId}
          onShare={handleShare}
          shareError={shareError}
        />

        <footer className="border-t border-[var(--color-border)] pt-4 text-xs leading-5 text-[var(--color-text-muted)]">
          协作夹仅保存脱敏案情摘要、带来源锚点的类案/文书引用与你填写的短字段；不保存裁判文书正文、起草正文或原始案情，也不生成任何案件综述、归纳结论或胜负预测。协作夹内容只在本浏览器内存与后端持久化之间流转，不写入本地存储、不随 URL 携带。协作夹默认仅本人可见；夹主可显式将其共享给团队（private 与 team 两级，仅改可见范围、不放开任何正文），非夹主成员只读访问。
        </footer>
      </div>
    </main>
  );
}

// --- 脱敏案情摘要：仅 SearchProfile 脱敏白名单子集（非原始口语化案情）---------------

function SearchProfileSummaryEditor({
  summary,
  onChange,
}: {
  summary: CasebookSearchProfileSummary | null;
  onChange: (next: CasebookSearchProfileSummary | null) => void;
}) {
  const value = summary ?? {};

  function patch(next: Partial<CasebookSearchProfileSummary>) {
    onChange({ ...value, ...next });
  }

  const keywordsText = Array.isArray(value.dispute_focus_keywords)
    ? value.dispute_focus_keywords.join("、")
    : "";

  return (
    <section
      aria-label="案情摘要"
      className="flex flex-col gap-4 rounded-[10px] border border-[var(--color-border)] bg-[var(--color-surface)] p-5 sm:p-6"
    >
      <div>
        <p className="text-sm font-medium text-[var(--color-text)]">案情摘要（脱敏子集，可选）</p>
        <p className="mt-1 text-xs text-[var(--color-text-muted)]">
          仅归集已脱敏的结构化要素（案由 / 地域 / 审级倾向 / 争议焦点关键词 / 已脱敏检索文本）。不要填写原始口语化案情或当事人信息——原始案情绝不上送服务器。
        </p>
      </div>
      <div className="grid gap-2 sm:grid-cols-2">
        <input
          type="text"
          aria-label="案由"
          value={value.case_cause ?? ""}
          onChange={(event) => patch({ case_cause: event.target.value || null })}
          placeholder="案由（如：买卖合同纠纷）"
          className="rounded-[8px] border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-sm text-[var(--color-text)] outline-none focus:border-[var(--color-brand)] focus:ring-2 focus:ring-[var(--color-brand-soft)]"
        />
        <input
          type="text"
          aria-label="地域"
          value={value.region ?? ""}
          onChange={(event) => patch({ region: event.target.value || null })}
          placeholder="地域（如：北京市）"
          className="rounded-[8px] border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-sm text-[var(--color-text)] outline-none focus:border-[var(--color-brand)] focus:ring-2 focus:ring-[var(--color-brand-soft)]"
        />
        <input
          type="text"
          aria-label="审级倾向"
          value={value.trial_level_preference ?? ""}
          onChange={(event) => patch({ trial_level_preference: event.target.value || null })}
          placeholder="审级倾向（如：二审）"
          className="rounded-[8px] border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-sm text-[var(--color-text)] outline-none focus:border-[var(--color-brand)] focus:ring-2 focus:ring-[var(--color-brand-soft)]"
        />
        <input
          type="text"
          aria-label="争议焦点关键词"
          value={keywordsText}
          onChange={(event) =>
            patch({
              dispute_focus_keywords: event.target.value
                ? event.target.value
                    .split(/[、,，\s]+/)
                    .map((k) => k.trim())
                    .filter((k) => k.length > 0)
                : null,
            })
          }
          placeholder="争议焦点关键词（顿号/逗号分隔）"
          className="rounded-[8px] border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-sm text-[var(--color-text)] outline-none focus:border-[var(--color-brand)] focus:ring-2 focus:ring-[var(--color-brand-soft)]"
        />
        <input
          type="text"
          aria-label="已脱敏检索文本"
          value={value.query_text ?? ""}
          onChange={(event) => patch({ query_text: event.target.value || null })}
          placeholder="已脱敏检索文本（可选）"
          className="sm:col-span-2 rounded-[8px] border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-sm text-[var(--color-text)] outline-none focus:border-[var(--color-brand)] focus:ring-2 focus:ring-[var(--color-brand-soft)]"
        />
      </div>
      {summary != null ? (
        <button
          type="button"
          onClick={() => onChange(null)}
          className="inline-flex h-8 w-fit items-center justify-center rounded-[8px] border border-[var(--color-border-strong)] bg-[var(--color-surface)] px-3 text-xs font-medium text-[var(--color-text-muted)] transition hover:bg-[var(--color-surface-muted)]"
        >
          清除摘要
        </button>
      ) : null}
    </section>
  );
}

// --- 归集区：选入带锚点的 CandidateRef / DraftDescriptor（只展示元数据 + 锚点出处，零正文）---
// 引用来自检索结果 / 类案清单 / 文书工作台（调用方通过内存通道传入）。本步提供手动选入入口：
// 类案填 case_id + source_chunk_id 构造带锚点引用；文书填 draft_id + 段落标题构造骨架引用。
// 无锚点引用 / 无骨架标题前端拦截不可加入（与后端「无锚点丢弃」一致）。绝不接收/渲染任何正文字段。

function CollectArea({
  candidateRefs,
  draftDescriptors,
  onAddCandidate,
  onRemoveCandidate,
  onAddDraft,
  onRemoveDraft,
}: {
  candidateRefs: CasebookCandidateRefView[];
  draftDescriptors: CasebookDraftDescriptorView[];
  onAddCandidate: (ref: CasebookCandidateRefView) => void;
  onRemoveCandidate: (index: number) => void;
  onAddDraft: (d: CasebookDraftDescriptorView) => void;
  onRemoveDraft: (index: number) => void;
}) {
  return (
    <section
      aria-label="归集区"
      className="flex flex-col gap-5 rounded-[10px] border border-[var(--color-border)] bg-[var(--color-surface)] p-5 sm:p-6"
    >
      <div>
        <p className="text-sm font-medium text-[var(--color-text)]">归集引用（类案 / 文书骨架）</p>
        <p className="mt-1 text-xs text-[var(--color-text-muted)]">
          选入来自检索 / 清单 / 文书工作台的引用，每个引用必须带可核验来源锚点；引用只携带元数据与出处，不含裁判文书 / 起草正文。无锚点的引用无法加入。
        </p>
      </div>

      <CandidateRefSection
        candidateRefs={candidateRefs}
        onAdd={onAddCandidate}
        onRemove={onRemoveCandidate}
      />

      <DraftDescriptorSection
        draftDescriptors={draftDescriptors}
        onAdd={onAddDraft}
        onRemove={onRemoveDraft}
      />
    </section>
  );
}

function CandidateRefSection({
  candidateRefs,
  onAdd,
  onRemove,
}: {
  candidateRefs: CasebookCandidateRefView[];
  onAdd: (ref: CasebookCandidateRefView) => void;
  onRemove: (index: number) => void;
}) {
  const [caseId, setCaseId] = useState("");
  const [sourceChunkId, setSourceChunkId] = useState("");
  const [caseNumber, setCaseNumber] = useState("");
  const [court, setCourt] = useState("");
  const [error, setError] = useState<string | null>(null);

  function handleAdd() {
    const ref: CasebookCandidateRefView = {
      case_id: caseId.trim(),
      case_number: caseNumber.trim() || null,
      court: court.trim() || null,
      trial_level: null,
      case_cause: null,
      judgment_date: null,
      source_anchors:
        caseId.trim() && sourceChunkId.trim()
          ? [{ case_id: caseId.trim(), source_chunk_id: sourceChunkId.trim() }]
          : [],
    };
    // 前端拦截：无锚点引用不可加入（与后端「无锚点丢弃」红线一致）。
    if (!hasCandidateAnchor(ref)) {
      setError("类案引用必须带来源锚点（案件 ID + 来源片段 ID），无锚点无法加入。");
      return;
    }
    onAdd(ref);
    setCaseId("");
    setSourceChunkId("");
    setCaseNumber("");
    setCourt("");
    setError(null);
  }

  return (
    <div className="flex flex-col gap-3">
      <p className="text-xs font-medium text-[var(--color-text-muted)]">类案引用（CandidateRef）</p>
      <div className="grid gap-2 sm:grid-cols-2">
        <input
          type="text"
          aria-label="类案 案件ID"
          value={caseId}
          onChange={(event) => setCaseId(event.target.value)}
          placeholder="案件 ID（必填）"
          className="rounded-[8px] border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-sm text-[var(--color-text)] outline-none focus:border-[var(--color-brand)] focus:ring-2 focus:ring-[var(--color-brand-soft)]"
        />
        <input
          type="text"
          aria-label="类案 来源片段ID"
          value={sourceChunkId}
          onChange={(event) => setSourceChunkId(event.target.value)}
          placeholder="来源片段 ID（必填，作为锚点）"
          className="rounded-[8px] border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-sm text-[var(--color-text)] outline-none focus:border-[var(--color-brand)] focus:ring-2 focus:ring-[var(--color-brand-soft)]"
        />
        <input
          type="text"
          aria-label="类案 案号"
          value={caseNumber}
          onChange={(event) => setCaseNumber(event.target.value)}
          placeholder="案号（可选元数据）"
          className="rounded-[8px] border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-sm text-[var(--color-text)] outline-none focus:border-[var(--color-brand)] focus:ring-2 focus:ring-[var(--color-brand-soft)]"
        />
        <input
          type="text"
          aria-label="类案 法院"
          value={court}
          onChange={(event) => setCourt(event.target.value)}
          placeholder="法院（可选元数据）"
          className="rounded-[8px] border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-sm text-[var(--color-text)] outline-none focus:border-[var(--color-brand)] focus:ring-2 focus:ring-[var(--color-brand-soft)]"
        />
      </div>
      {error ? (
        <p role="alert" className="text-xs text-[var(--color-danger)]">
          {error}
        </p>
      ) : null}
      <button
        type="button"
        onClick={handleAdd}
        className="inline-flex h-9 w-fit items-center justify-center rounded-[8px] border border-[var(--color-border-strong)] bg-[var(--color-surface)] px-3 text-sm font-medium text-[var(--color-brand)] transition hover:bg-[var(--color-brand-soft)]"
      >
        加入类案引用
      </button>

      {candidateRefs.length > 0 ? (
        <ul className="grid gap-2">
          {candidateRefs.map((ref, index) => (
            <li
              key={`${ref.case_id}-${index}`}
              className="rounded-[8px] border border-[var(--color-border)] bg-[var(--color-surface-muted)] p-3"
            >
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <p className="break-words font-mono text-sm text-[var(--color-text)]">
                    {ref.case_id}
                  </p>
                  <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-xs leading-5 text-[var(--color-text-muted)]">
                    {ref.case_number ? <span>{ref.case_number}</span> : null}
                    {ref.court ? <span>{ref.court}</span> : null}
                  </div>
                  <div className="mt-1 flex flex-wrap gap-2">
                    {(ref.source_anchors ?? []).map((a) => (
                      <span
                        key={`${a.case_id}-${a.source_chunk_id}`}
                        className="rounded-[4px] bg-[var(--color-surface)] px-2 py-1 font-mono text-[11px] text-[var(--color-text-muted)]"
                      >
                        来源 {a.source_chunk_id}
                      </span>
                    ))}
                  </div>
                </div>
                <button
                  type="button"
                  aria-label={`移除类案引用 ${ref.case_id}`}
                  onClick={() => onRemove(index)}
                  className="inline-flex h-8 shrink-0 items-center justify-center rounded-[8px] border border-[var(--color-border-strong)] bg-[var(--color-surface)] px-2 text-xs font-medium text-[var(--color-text-muted)] transition hover:bg-[var(--color-surface)]"
                >
                  移除
                </button>
              </div>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

function DraftDescriptorSection({
  draftDescriptors,
  onAdd,
  onRemove,
}: {
  draftDescriptors: CasebookDraftDescriptorView[];
  onAdd: (d: CasebookDraftDescriptorView) => void;
  onRemove: (index: number) => void;
}) {
  const [draftId, setDraftId] = useState("");
  const [titlesText, setTitlesText] = useState("");
  const [error, setError] = useState<string | null>(null);

  function handleAdd() {
    const skeleton = titlesText
      .split(/[\n;；]+/)
      .map((s) => s.trim())
      .filter((s) => s.length > 0);
    // 前端拦截：无段落标题骨架不可加入（与后端「无骨架丢弃」一致）。
    if (skeleton.length === 0) {
      setError("文书骨架引用必须至少含一个段落标题，且只填标题不起草正文。");
      return;
    }
    const d: CasebookDraftDescriptorView = {
      draft_id: draftId.trim() || null,
      structure_skeleton: skeleton,
      candidate_refs: [],
      statute_refs: [],
      note: null,
      tag: null,
    };
    onAdd(d);
    setDraftId("");
    setTitlesText("");
    setError(null);
  }

  return (
    <div className="flex flex-col gap-3 border-t border-[var(--color-border)] pt-4">
      <p className="text-xs font-medium text-[var(--color-text-muted)]">
        文书骨架引用（DraftDescriptor，来自文书工作台）
      </p>
      <div className="grid gap-2">
        <input
          type="text"
          aria-label="文书 草稿ID"
          value={draftId}
          onChange={(event) => setDraftId(event.target.value)}
          placeholder="草稿 ID（可选，来自文书工作台）"
          className="rounded-[8px] border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-sm text-[var(--color-text)] outline-none focus:border-[var(--color-brand)] focus:ring-2 focus:ring-[var(--color-brand-soft)]"
        />
        <textarea
          aria-label="文书 段落标题"
          value={titlesText}
          onChange={(event) => setTitlesText(event.target.value)}
          placeholder="段落标题骨架（每行/分号一项，只填标题不起草正文）"
          className="min-h-[64px] max-h-[160px] resize-y rounded-[8px] border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-sm leading-6 text-[var(--color-text)] outline-none focus:border-[var(--color-brand)] focus:ring-2 focus:ring-[var(--color-brand-soft)]"
        />
      </div>
      {error ? (
        <p role="alert" className="text-xs text-[var(--color-danger)]">
          {error}
        </p>
      ) : null}
      <button
        type="button"
        onClick={handleAdd}
        className="inline-flex h-9 w-fit items-center justify-center rounded-[8px] border border-[var(--color-border-strong)] bg-[var(--color-surface)] px-3 text-sm font-medium text-[var(--color-brand)] transition hover:bg-[var(--color-brand-soft)]"
      >
        加入文书骨架引用
      </button>

      {draftDescriptors.length > 0 ? (
        <ul className="grid gap-2">
          {draftDescriptors.map((d, index) => (
            <li
              key={`${d.draft_id ?? "draft"}-${index}`}
              className="rounded-[8px] border border-[var(--color-border)] bg-[var(--color-surface-muted)] p-3"
            >
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <p className="break-words text-sm text-[var(--color-text)]">
                    {d.structure_skeleton[0] || "（未命名骨架）"}
                  </p>
                  <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-xs leading-5 text-[var(--color-text-muted)]">
                    <span>{d.structure_skeleton.length} 个段落标题</span>
                    {d.draft_id ? (
                      <span className="font-mono">#{d.draft_id}</span>
                    ) : null}
                  </div>
                </div>
                <button
                  type="button"
                  aria-label={`移除文书骨架引用 ${d.draft_id ?? index}`}
                  onClick={() => onRemove(index)}
                  className="inline-flex h-8 shrink-0 items-center justify-center rounded-[8px] border border-[var(--color-border-strong)] bg-[var(--color-surface)] px-2 text-xs font-medium text-[var(--color-text-muted)] transition hover:bg-[var(--color-surface)]"
                >
                  移除
                </button>
              </div>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

// --- 用户短字段：title / note / tag（短，仅元数据，非正文）-------------------------

function ShortFields({
  title,
  onTitleChange,
  note,
  onNoteChange,
  tag,
  onTagChange,
}: {
  title: string;
  onTitleChange: (value: string) => void;
  note: string;
  onNoteChange: (value: string) => void;
  tag: string;
  onTagChange: (value: string) => void;
}) {
  return (
    <section
      aria-label="协作夹短字段"
      className="flex flex-col gap-4 rounded-[10px] border border-[var(--color-border)] bg-[var(--color-surface)] p-5 sm:p-6"
    >
      <div>
        <label htmlFor="folder-title" className="text-sm font-medium text-[var(--color-text)]">
          协作夹标题（可选，短）
        </label>
        <input
          id="folder-title"
          type="text"
          value={title}
          maxLength={TITLE_MAX_LEN}
          onChange={(event) => onTitleChange(event.target.value)}
          className="mt-2 block w-full rounded-[8px] border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-sm text-[var(--color-text)] outline-none focus:border-[var(--color-brand)] focus:ring-2 focus:ring-[var(--color-brand-soft)]"
          placeholder={`给本协作夹的标题（不超过 ${TITLE_MAX_LEN} 字，仅作元数据）`}
        />
      </div>
      <div>
        <label htmlFor="folder-note" className="text-sm font-medium text-[var(--color-text)]">
          备注（可选，短）
        </label>
        <textarea
          id="folder-note"
          value={note}
          maxLength={NOTE_MAX_LEN}
          onChange={(event) => onNoteChange(event.target.value)}
          className="mt-2 block min-h-[64px] max-h-[160px] w-full resize-y rounded-[8px] border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-sm leading-6 text-[var(--color-text)] outline-none focus:border-[var(--color-brand)] focus:ring-2 focus:ring-[var(--color-brand-soft)]"
          placeholder="给本协作夹的简短备注（不超过 200 字，仅作元数据，不写入正文）"
        />
        <p className="mt-1 text-right text-xs text-[var(--color-text-muted)]">
          {note.length}/{NOTE_MAX_LEN}
        </p>
      </div>
      <div>
        <label htmlFor="folder-tag" className="text-sm font-medium text-[var(--color-text)]">
          标签（可选）
        </label>
        <input
          id="folder-tag"
          type="text"
          value={tag}
          maxLength={TAG_MAX_LEN}
          onChange={(event) => onTagChange(event.target.value)}
          className="mt-2 block w-full rounded-[8px] border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-sm text-[var(--color-text)] outline-none focus:border-[var(--color-brand)] focus:ring-2 focus:ring-[var(--color-brand-soft)]"
          placeholder="例如：买卖合同 / 二审（不超过 40 字）"
        />
      </div>
    </section>
  );
}

// --- 保存/更新栏（无任何起草/生成/归纳/预测入口）--------------------------------

function SaveBar({
  editing,
  canSave,
  status,
  onSave,
  onReset,
}: {
  editing: boolean;
  canSave: boolean;
  status: SaveStatus;
  onSave: () => void;
  onReset: () => void;
}) {
  return (
    <section aria-label="保存协作夹" className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-3">
        <button
          type="button"
          onClick={onSave}
          disabled={!canSave}
          className="inline-flex h-10 items-center justify-center rounded-[8px] bg-[var(--color-brand)] px-4 text-sm font-semibold text-[var(--color-on-brand,#fff)] transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {status.kind === "saving" ? "保存中…" : editing ? "更新协作夹" : "保存协作夹"}
        </button>
        <button
          type="button"
          onClick={onReset}
          className="inline-flex h-10 items-center justify-center rounded-[8px] border border-[var(--color-border-strong)] bg-[var(--color-surface)] px-4 text-sm font-medium text-[var(--color-text)] transition hover:bg-[var(--color-surface-muted)]"
        >
          新建空白协作夹
        </button>
        {status.kind === "saved" ? (
          <span className="text-xs text-[var(--color-text-muted)]">已保存（默认仅本人可见）。</span>
        ) : null}
      </div>
      {status.kind === "error" ? (
        <p
          role="alert"
          className="rounded-[8px] border border-[var(--color-danger)] bg-[var(--color-danger-soft)] px-3 py-2 text-sm text-[var(--color-danger)]"
        >
          {status.message}
        </p>
      ) : null}
    </section>
  );
}

// --- 已保存协作夹列表（仅本人/团队可见，只读展示 visibility，零正文）---------------

function SavedFolders({
  folders,
  listError,
  onLoad,
  currentUserId,
  activeTeamId,
  onActiveTeamIdChange,
  onShare,
  shareError,
}: {
  folders: CaseFolderView[];
  listError: string | null;
  onLoad: (folder: CaseFolderView) => void;
  currentUserId: string | null;
  activeTeamId: string;
  onActiveTeamIdChange: (value: string) => void;
  onShare: (folder: CaseFolderView, visibility: "private" | "team", teamId: string) => void;
  shareError: string | null;
}) {
  return (
    <section
      aria-label="已保存协作夹"
      className="flex flex-col gap-3 rounded-[10px] border border-[var(--color-border)] bg-[var(--color-surface)] p-5 sm:p-6"
    >
      <p className="text-sm font-medium text-[var(--color-text)]">已保存协作夹（仅本人/团队可见）</p>
      <p className="text-xs text-[var(--color-text-muted)]">
        协作夹仅含脱敏摘要、引用元数据与来源锚点、你的短字段；不含裁判文书 / 起草正文，也不含案件综述、归纳结论或胜负预测。默认仅本人可见；夹主可显式共享给团队（仅改可见范围，不放开任何正文）。
      </p>

      <TeamContextBar activeTeamId={activeTeamId} onActiveTeamIdChange={onActiveTeamIdChange} />

      {shareError ? (
        <p
          role="alert"
          className="rounded-[8px] border border-[var(--color-danger)] bg-[var(--color-danger-soft)] px-3 py-2 text-xs text-[var(--color-danger)]"
        >
          {shareError}
        </p>
      ) : null}

      {listError ? (
        <p className="text-xs text-[var(--color-text-muted)]">{listError}</p>
      ) : folders.length === 0 ? (
        <p className="text-xs text-[var(--color-text-subtle)]">暂无已保存协作夹。</p>
      ) : (
        <ul className="grid gap-2">
          {folders.map((folder) => {
            const isOwner = currentUserId != null && folder.owner_user_id === currentUserId;
            return (
              <li
                key={folder.case_folder_id}
                className="flex flex-col gap-2 rounded-[8px] border border-[var(--color-border)] bg-[var(--color-surface-muted)] p-3"
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <p className="break-words text-sm text-[var(--color-text)]">
                      {folder.title || "（未命名协作夹）"}
                    </p>
                    <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-xs leading-5 text-[var(--color-text-muted)]">
                      <span>{folder.candidate_refs.length} 条类案引用</span>
                      <span>{folder.draft_descriptors.length} 条文书骨架</span>
                      {folder.search_profile_summary ? <span>含案情摘要</span> : null}
                      {folder.tag ? <span>#{folder.tag}</span> : null}
                    </div>
                  </div>
                  <div className="flex shrink-0 items-center gap-2">
                    <span className="inline-flex h-7 items-center rounded-[4px] border border-[var(--color-border)] px-2 text-[11px] font-medium text-[var(--color-text-muted)]">
                      {VISIBILITY_LABEL[folder.visibility] ?? folder.visibility}
                    </span>
                    <button
                      type="button"
                      onClick={() => onLoad(folder)}
                      className="inline-flex h-8 items-center justify-center rounded-[8px] border border-[var(--color-border-strong)] bg-[var(--color-surface)] px-3 text-xs font-medium text-[var(--color-brand)] transition hover:bg-[var(--color-brand-soft)]"
                    >
                      载入编辑
                    </button>
                  </div>
                </div>
                {isOwner ? (
                  <ShareControl folder={folder} activeTeamId={activeTeamId} onShare={onShare} />
                ) : (
                  <p className="text-[11px] text-[var(--color-text-subtle)]">
                    由协作夹所有者管理共享，你当前为只读访问。
                  </p>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

// 团队态输入：填团队 ID 后以团队成员身份列出（X-Team-Id），看到本团队 visibility=team 协作夹。
function TeamContextBar({
  activeTeamId,
  onActiveTeamIdChange,
}: {
  activeTeamId: string;
  onActiveTeamIdChange: (value: string) => void;
}) {
  return (
    <div className="flex flex-col gap-1 border-b border-[var(--color-border)] pb-3">
      <label htmlFor="casebook-team-context" className="text-xs font-medium text-[var(--color-text-muted)]">
        团队上下文（可选）
      </label>
      <input
        id="casebook-team-context"
        type="text"
        value={activeTeamId}
        onChange={(event) => onActiveTeamIdChange(event.target.value)}
        placeholder="填入团队 ID 以查看本团队共享的协作夹（留空仅看本人私有）"
        className="w-full rounded-[8px] border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-xs text-[var(--color-text)] outline-none focus:border-[var(--color-brand)] focus:ring-2 focus:ring-[var(--color-brand-soft)]"
      />
    </div>
  );
}

// owner-only 共享控件：在 private<->team 间显式切换可见性（只改 visibility 元数据，零正文）。
function ShareControl({
  folder,
  activeTeamId,
  onShare,
}: {
  folder: CaseFolderView;
  activeTeamId: string;
  onShare: (folder: CaseFolderView, visibility: "private" | "team", teamId: string) => void;
}) {
  const [teamId, setTeamId] = useState(folder.team_id ?? activeTeamId ?? "");
  const isTeam = folder.visibility === "team";

  return (
    <div className="flex flex-col gap-2 border-t border-[var(--color-border)] pt-2">
      <p className="text-[11px] font-medium text-[var(--color-text-muted)]">
        共享范围（仅本协作夹所有者可改）
      </p>
      {isTeam ? (
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-[11px] text-[var(--color-text-muted)]">当前已共享给团队。</span>
          <button
            type="button"
            onClick={() => onShare(folder, "private", "")}
            className="inline-flex h-8 items-center justify-center rounded-[8px] border border-[var(--color-border-strong)] bg-[var(--color-surface)] px-3 text-xs font-medium text-[var(--color-text)] transition hover:bg-[var(--color-surface-muted)]"
          >
            改回仅本人可见
          </button>
        </div>
      ) : (
        <div className="flex flex-wrap items-center gap-2">
          <input
            type="text"
            aria-label={`协作夹 ${folder.case_folder_id} 共享团队ID`}
            value={teamId}
            onChange={(event) => setTeamId(event.target.value)}
            placeholder="团队 ID"
            className="h-8 w-40 rounded-[8px] border border-[var(--color-border)] bg-[var(--color-surface)] px-2 text-xs text-[var(--color-text)] outline-none focus:border-[var(--color-brand)] focus:ring-2 focus:ring-[var(--color-brand-soft)]"
          />
          <button
            type="button"
            disabled={teamId.trim().length === 0}
            onClick={() => onShare(folder, "team", teamId.trim())}
            className="inline-flex h-8 items-center justify-center rounded-[8px] border border-[var(--color-border-strong)] bg-[var(--color-surface)] px-3 text-xs font-medium text-[var(--color-brand)] transition hover:bg-[var(--color-brand-soft)] disabled:cursor-not-allowed disabled:opacity-50"
          >
            共享给团队
          </button>
        </div>
      )}
    </div>
  );
}