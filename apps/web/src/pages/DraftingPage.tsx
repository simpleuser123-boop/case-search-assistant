import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { isDraftingEnabled } from "../config/featureFlags";
import {
  createDraft,
  listDrafts,
  updateDraft,
  hasCandidateAnchor,
  hasStatuteAnchor,
  STRUCTURE_SKELETON_ITEM_MAX_LEN,
  STRUCTURE_SKELETON_MAX_ITEMS,
  NOTE_MAX_LEN,
  TAG_MAX_LEN,
  type DraftCandidateRefView,
  type DraftStatuteRefView,
  type DraftDescriptorView,
  type DraftMutationResult,
  type DraftListResult,
} from "../services/draftingApi";
import {
  exportDraft,
  logDraftExport,
  type DraftExportFormat,
  type DraftExportStatus,
} from "../lib/draftingExport";

// 文书工作台：用户编排 structure_skeleton（段落标题）+ 选入带锚点的 CandidateRef/StatuteRef
// （来自检索/清单/法条互跳，只携带元数据 + 锚点出处，零正文）+ 自填 note/tag，调 E6-2 端点
// 组装为 DraftDescriptor（只组装、不起草）。
//
// 红线：
// - 草稿态（骨架/引用/短字段）只存本组件 React state（内存），绝不写浏览器存储、绝不入 URL。
// - 引用只渲染元数据 + 锚点出处，绝不渲染裁判文书/候选/chunk 正文；无锚点引用前端拦截不可加入。
// - 本页**无任何** AI 起草 / 生成段落 / 生成结论 / 预测胜负的入口或调用——只搬运用户编排的标题与引用。

type SaveStatus =
  | { kind: "idle" }
  | { kind: "saving" }
  | { kind: "error"; message: string }
  | { kind: "saved" };

const DISABLED_REASON_TEXT: Record<string, string> = {
  DRAFTING_DISABLED: "文书工作台当前未启用（后端 ENABLE_DRAFTING=false）。",
};

// 导出状态 -> 用户安全提示（失败/降级不抛错、不影响主链路）。
const EXPORT_STATUS_TEXT: Record<DraftExportStatus, string> = {
  exported: "已导出（仅含元数据、来源锚点与备注，强制免责头；请人工复核）。",
  empty: "该草稿暂无可导出的段落标题，已跳过导出。",
  degraded: "当前环境无法直接下载，已生成内容但未触发下载，请稍后重试或更换浏览器。",
  failed: "导出未成功，请稍后重试；本操作不影响已保存的草稿。",
};

export function DraftingPage() {
  // flag off：DOM 不渲染任何文书工作台入口/页面（安全末态，三重门控之第三重）。
  if (!isDraftingEnabled()) {
    return null;
  }
  return <DraftingWorkspace />;
}

function describeError(
  result: Extract<DraftMutationResult | DraftListResult, { ok: false }>,
): string {
  if (result.reason === "disabled") {
    return (
      (result.reasonCode && DISABLED_REASON_TEXT[result.reasonCode]) ||
      "文书工作台当前未启用，请稍后再试或联系管理员。"
    );
  }
  if (result.reason === "login_required") {
    return "文书工作台操作需先登录。";
  }
  if (result.reason === "timeout") {
    return "请求超时，请稍后重试。";
  }
  if (result.reason === "rejected") {
    return result.message || "草稿内容未通过校验（引用须带锚点、标题非正文），请调整后重试。";
  }
  return "服务暂时不可用，请稍后重试。";
}

function DraftingWorkspace() {
  // 全部为内存 state：绝不入存储 / 不入 URL。
  const [skeleton, setSkeleton] = useState<string[]>([""]);
  const [candidateRefs, setCandidateRefs] = useState<DraftCandidateRefView[]>([]);
  const [statuteRefs, setStatuteRefs] = useState<DraftStatuteRefView[]>([]);
  const [note, setNote] = useState("");
  const [tag, setTag] = useState("");

  const [editingDraftId, setEditingDraftId] = useState<string | null>(null);
  const [status, setStatus] = useState<SaveStatus>({ kind: "idle" });
  const [drafts, setDrafts] = useState<DraftDescriptorView[]>([]);
  const [listError, setListError] = useState<string | null>(null);
  // 导出提示（按 draft_id 维度），失败/降级只提示不抛错。
  const [exportNotice, setExportNotice] = useState<{ draftId: string; message: string } | null>(
    null,
  );

  const refreshDrafts = useCallback(async () => {
    const result = await listDrafts();
    if (result.ok) {
      setDrafts(result.data.drafts ?? []);
      setListError(null);
      return;
    }
    setDrafts([]);
    setListError(describeError(result));
  }, []);

  useEffect(() => {
    void refreshDrafts();
  }, [refreshDrafts]);

  // 导出：基于已取的 DraftDescriptor（零正文）本地生成文件，强制免责头；
  // 无锚点引用在 exportDraft 内被丢弃；任何异常安全降级为提示，不影响主链路。
  function handleExport(draft: DraftDescriptorView, format: DraftExportFormat) {
    const result = exportDraft(draft, { format });
    logDraftExport(result.descriptor);
    setExportNotice({
      draftId: draft.draft_id,
      message: EXPORT_STATUS_TEXT[result.descriptor.export_status],
    });
  }

  const trimmedTitles = skeleton.map((t) => t.trim()).filter((t) => t.length > 0);
  const canSave = trimmedTitles.length > 0 && status.kind !== "saving";

  function resetForm() {
    setSkeleton([""]);
    setCandidateRefs([]);
    setStatuteRefs([]);
    setNote("");
    setTag("");
    setEditingDraftId(null);
    setStatus({ kind: "idle" });
  }

  async function handleSave() {
    if (!canSave) {
      return;
    }
    setStatus({ kind: "saving" });
    const input = {
      structure_skeleton: skeleton,
      candidate_refs: candidateRefs,
      statute_refs: statuteRefs,
      note,
      tag,
    };
    const result = editingDraftId
      ? await updateDraft(editingDraftId, input)
      : await createDraft(input);
    if (result.ok) {
      setStatus({ kind: "saved" });
      setEditingDraftId(result.data.draft_id);
      void refreshDrafts();
      return;
    }
    setStatus({ kind: "error", message: describeError(result) });
  }

  function handleLoadDraft(draft: DraftDescriptorView) {
    // 把已保存草稿载入编辑态（仍只是元数据 + 引用 + 短字段，零正文）。
    setSkeleton(draft.structure_skeleton.length > 0 ? [...draft.structure_skeleton] : [""]);
    setCandidateRefs([...(draft.candidate_refs ?? [])]);
    setStatuteRefs([...(draft.statute_refs ?? [])]);
    setNote(draft.note ?? "");
    setTag(draft.tag ?? "");
    setEditingDraftId(draft.draft_id);
    setStatus({ kind: "idle" });
  }

  return (
    <main
      aria-label="文书工作台"
      className="min-h-[100dvh] bg-[var(--color-bg)] px-4 py-6 text-[var(--color-text)] sm:px-6 sm:py-10"
    >
      <div className="mx-auto flex w-full max-w-[1180px] flex-col gap-8">
        <header className="flex items-start justify-between border-b border-[var(--color-border)] pb-4">
          <div>
            <p className="text-base font-semibold text-[var(--color-text)]">文书工作台</p>
            <p className="mt-1 text-xs text-[var(--color-text-muted)]">
              组装结构骨架（段落标题）+ 带来源锚点的类案/法条引用 + 备注；只组装锚定来源、不起草结论
            </p>
          </div>
          <Link
            to="/"
            className="hidden rounded-[8px] border border-[var(--color-border-strong)] px-3 py-1.5 text-xs font-medium text-[var(--color-brand)] transition hover:bg-[var(--color-brand-soft)] sm:inline-flex"
          >
            返回检索助手
          </Link>
        </header>

        <SkeletonEditor skeleton={skeleton} onChange={setSkeleton} />

        <ReferencePicker
          candidateRefs={candidateRefs}
          statuteRefs={statuteRefs}
          onAddCandidate={(ref) => setCandidateRefs((prev) => [...prev, ref])}
          onRemoveCandidate={(index) =>
            setCandidateRefs((prev) => prev.filter((_, i) => i !== index))
          }
          onAddStatute={(ref) => setStatuteRefs((prev) => [...prev, ref])}
          onRemoveStatute={(index) =>
            setStatuteRefs((prev) => prev.filter((_, i) => i !== index))
          }
        />

        <ShortFields note={note} onNoteChange={setNote} tag={tag} onTagChange={setTag} />

        <SaveBar
          editing={editingDraftId !== null}
          canSave={canSave}
          status={status}
          onSave={handleSave}
          onReset={resetForm}
        />

        <SavedDrafts
          drafts={drafts}
          listError={listError}
          onLoad={handleLoadDraft}
          onExport={handleExport}
          exportNotice={exportNotice}
        />

        <footer className="border-t border-[var(--color-border)] pt-4 text-xs leading-5 text-[var(--color-text-muted)]">
          草稿仅保存结构骨架（段落标题）、带来源锚点的引用与你填写的短备注；不保存裁判文书正文、不生成任何段落正文或结论。草稿内容只在本浏览器内存与后端持久化之间流转，不写入本地存储、不随 URL 携带。本工具不起草法律文书、不预测裁判结果，导出与最终文书需经人工复核。
        </footer>
      </div>
    </main>
  );
}

// --- 结构骨架编排：纯标题增删改（无「自动起草段落正文」能力）---------------------

function SkeletonEditor({
  skeleton,
  onChange,
}: {
  skeleton: string[];
  onChange: (next: string[]) => void;
}) {
  function updateAt(index: number, value: string) {
    onChange(skeleton.map((item, i) => (i === index ? value : item)));
  }
  function removeAt(index: number) {
    const next = skeleton.filter((_, i) => i !== index);
    onChange(next.length > 0 ? next : [""]);
  }
  function addItem() {
    if (skeleton.length >= STRUCTURE_SKELETON_MAX_ITEMS) {
      return;
    }
    onChange([...skeleton, ""]);
  }

  return (
    <section
      aria-label="结构骨架"
      className="flex flex-col gap-4 rounded-[10px] border border-[var(--color-border)] bg-[var(--color-surface)] p-5 sm:p-6"
    >
      <div>
        <p className="text-sm font-medium text-[var(--color-text)]">结构骨架（段落标题）</p>
        <p className="mt-1 text-xs text-[var(--color-text-muted)]">
          按文书结构编排段落标题（如「一、基本案情」「二、争议焦点」「三、参考类案」）。这里只填标题，不起草段落正文；单项不超过 {STRUCTURE_SKELETON_ITEM_MAX_LEN} 字，最多 {STRUCTURE_SKELETON_MAX_ITEMS} 项。
        </p>
      </div>

      <ol className="flex flex-col gap-2">
        {skeleton.map((item, index) => {
          const overLimit = item.trim().length > STRUCTURE_SKELETON_ITEM_MAX_LEN;
          return (
            <li key={index} className="flex items-start gap-2">
              <span className="mt-2 w-6 shrink-0 text-right font-mono text-xs text-[var(--color-text-muted)]">
                {index + 1}.
              </span>
              <div className="min-w-0 flex-1">
                <input
                  type="text"
                  aria-label={`段落标题 ${index + 1}`}
                  value={item}
                  maxLength={STRUCTURE_SKELETON_ITEM_MAX_LEN}
                  onChange={(event) => updateAt(index, event.target.value)}
                  className="block w-full rounded-[8px] border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-sm leading-6 text-[var(--color-text)] outline-none transition focus:border-[var(--color-brand)] focus:ring-2 focus:ring-[var(--color-brand-soft)]"
                  placeholder="例如：二、争议焦点"
                />
                {overLimit ? (
                  <p className="mt-1 text-xs text-[var(--color-danger)]">
                    标题应为段落标题而非正文，请控制在 {STRUCTURE_SKELETON_ITEM_MAX_LEN} 字内。
                  </p>
                ) : null}
              </div>
              <button
                type="button"
                aria-label={`删除段落标题 ${index + 1}`}
                onClick={() => removeAt(index)}
                className="mt-1 inline-flex h-8 shrink-0 items-center justify-center rounded-[8px] border border-[var(--color-border-strong)] bg-[var(--color-surface)] px-2 text-xs font-medium text-[var(--color-text-muted)] transition hover:bg-[var(--color-surface-muted)]"
              >
                删除
              </button>
            </li>
          );
        })}
      </ol>

      <button
        type="button"
        onClick={addItem}
        disabled={skeleton.length >= STRUCTURE_SKELETON_MAX_ITEMS}
        className="inline-flex h-9 w-fit items-center justify-center rounded-[8px] border border-[var(--color-border-strong)] bg-[var(--color-surface)] px-3 text-sm font-medium text-[var(--color-brand)] transition hover:bg-[var(--color-brand-soft)] disabled:cursor-not-allowed disabled:opacity-50"
      >
        添加段落标题
      </button>
    </section>
  );
}

// --- 引用区：选入带锚点的 CandidateRef/StatuteRef（只展示元数据 + 锚点出处，零正文）------
// 引用来自检索结果 / 类案清单 / 法条互跳（调用方通过 location.state 等内存通道传入）。本步提供
// 手动选入入口：用户填 case_id + source_chunk_id（类案）/ statute_id + text_id（法条）构造**带锚点**
// 引用；无锚点引用前端拦截不可加入（与后端「无锚点丢弃」一致）。绝不接收 / 渲染任何正文字段。

function ReferencePicker({
  candidateRefs,
  statuteRefs,
  onAddCandidate,
  onRemoveCandidate,
  onAddStatute,
  onRemoveStatute,
}: {
  candidateRefs: DraftCandidateRefView[];
  statuteRefs: DraftStatuteRefView[];
  onAddCandidate: (ref: DraftCandidateRefView) => void;
  onRemoveCandidate: (index: number) => void;
  onAddStatute: (ref: DraftStatuteRefView) => void;
  onRemoveStatute: (index: number) => void;
}) {
  return (
    <section
      aria-label="引用区"
      className="flex flex-col gap-5 rounded-[10px] border border-[var(--color-border)] bg-[var(--color-surface)] p-5 sm:p-6"
    >
      <div>
        <p className="text-sm font-medium text-[var(--color-text)]">参考引用（类案 / 法条）</p>
        <p className="mt-1 text-xs text-[var(--color-text-muted)]">
          选入来自检索 / 清单 / 法条互跳的引用，每个引用必须带可核验来源锚点；引用只携带元数据与出处，不含裁判文书正文。无锚点的引用无法加入。
        </p>
      </div>

      <CandidateRefSection
        candidateRefs={candidateRefs}
        onAdd={onAddCandidate}
        onRemove={onRemoveCandidate}
      />

      <StatuteRefSection
        statuteRefs={statuteRefs}
        onAdd={onAddStatute}
        onRemove={onRemoveStatute}
      />
    </section>
  );
}

function CandidateRefSection({
  candidateRefs,
  onAdd,
  onRemove,
}: {
  candidateRefs: DraftCandidateRefView[];
  onAdd: (ref: DraftCandidateRefView) => void;
  onRemove: (index: number) => void;
}) {
  const [caseId, setCaseId] = useState("");
  const [sourceChunkId, setSourceChunkId] = useState("");
  const [caseNumber, setCaseNumber] = useState("");
  const [court, setCourt] = useState("");
  const [error, setError] = useState<string | null>(null);

  function handleAdd() {
    const ref: DraftCandidateRefView = {
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

function StatuteRefSection({
  statuteRefs,
  onAdd,
  onRemove,
}: {
  statuteRefs: DraftStatuteRefView[];
  onAdd: (ref: DraftStatuteRefView) => void;
  onRemove: (index: number) => void;
}) {
  const [statuteId, setStatuteId] = useState("");
  const [lawName, setLawName] = useState("");
  const [articleNo, setArticleNo] = useState("");
  const [textId, setTextId] = useState("");
  const [error, setError] = useState<string | null>(null);

  function handleAdd() {
    const ref: DraftStatuteRefView = {
      statute_id: statuteId.trim(),
      law_name: lawName.trim(),
      article_no: articleNo.trim() || null,
      statute_anchors: textId.trim()
        ? [
            {
              text_id: textId.trim(),
              law_name: lawName.trim() || null,
              article_no: articleNo.trim() || null,
            },
          ]
        : [],
      article_text: null,
      source_corpus: null,
      effective_status: null,
      related_case_refs: [],
    };
    // 前端拦截：无锚点法条引用不可加入；条文正文不由前端生成（article_text 恒为 null，等待语料回填）。
    if (!hasStatuteAnchor(ref) || !ref.statute_id || !ref.law_name) {
      setError("法条引用必须带 text_id 来源锚点，且需填写法条 ID 与法律名称。");
      return;
    }
    onAdd(ref);
    setStatuteId("");
    setLawName("");
    setArticleNo("");
    setTextId("");
    setError(null);
  }

  return (
    <div className="flex flex-col gap-3 border-t border-[var(--color-border)] pt-4">
      <p className="text-xs font-medium text-[var(--color-text-muted)]">法条引用（StatuteRef，经互跳）</p>
      <div className="grid gap-2 sm:grid-cols-2">
        <input
          type="text"
          aria-label="法条 法条ID"
          value={statuteId}
          onChange={(event) => setStatuteId(event.target.value)}
          placeholder="法条 ID（必填）"
          className="rounded-[8px] border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-sm text-[var(--color-text)] outline-none focus:border-[var(--color-brand)] focus:ring-2 focus:ring-[var(--color-brand-soft)]"
        />
        <input
          type="text"
          aria-label="法条 来源text_id"
          value={textId}
          onChange={(event) => setTextId(event.target.value)}
          placeholder="来源 text_id（必填，作为锚点）"
          className="rounded-[8px] border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-sm text-[var(--color-text)] outline-none focus:border-[var(--color-brand)] focus:ring-2 focus:ring-[var(--color-brand-soft)]"
        />
        <input
          type="text"
          aria-label="法条 法律名称"
          value={lawName}
          onChange={(event) => setLawName(event.target.value)}
          placeholder="法律名称（必填）"
          className="rounded-[8px] border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-sm text-[var(--color-text)] outline-none focus:border-[var(--color-brand)] focus:ring-2 focus:ring-[var(--color-brand-soft)]"
        />
        <input
          type="text"
          aria-label="法条 条号"
          value={articleNo}
          onChange={(event) => setArticleNo(event.target.value)}
          placeholder="条号（可选元数据）"
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
        加入法条引用
      </button>

      {statuteRefs.length > 0 ? (
        <ul className="grid gap-2">
          {statuteRefs.map((ref, index) => (
            <li
              key={`${ref.statute_id}-${index}`}
              className="rounded-[8px] border border-[var(--color-border)] bg-[var(--color-surface-muted)] p-3"
            >
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <p className="break-words text-sm text-[var(--color-text)]">
                    {ref.law_name}
                    {ref.article_no ? (
                      <span className="ml-2 font-mono text-[var(--color-text-muted)]">
                        {ref.article_no}
                      </span>
                    ) : null}
                  </p>
                  <div className="mt-1 flex flex-wrap gap-2">
                    {(ref.statute_anchors ?? []).map((a) => (
                      <span
                        key={a.text_id}
                        className="rounded-[4px] bg-[var(--color-surface)] px-2 py-1 font-mono text-[11px] text-[var(--color-text-muted)]"
                      >
                        {a.text_id}
                      </span>
                    ))}
                  </div>
                </div>
                <button
                  type="button"
                  aria-label={`移除法条引用 ${ref.statute_id}`}
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

// --- 用户短字段：note / tag（短，仅元数据，非正文）-------------------------------

function ShortFields({
  note,
  onNoteChange,
  tag,
  onTagChange,
}: {
  note: string;
  onNoteChange: (value: string) => void;
  tag: string;
  onTagChange: (value: string) => void;
}) {
  return (
    <section
      aria-label="备注与标签"
      className="flex flex-col gap-4 rounded-[10px] border border-[var(--color-border)] bg-[var(--color-surface)] p-5 sm:p-6"
    >
      <div>
        <label htmlFor="draft-note" className="text-sm font-medium text-[var(--color-text)]">
          备注（可选，短）
        </label>
        <textarea
          id="draft-note"
          value={note}
          maxLength={NOTE_MAX_LEN}
          onChange={(event) => onNoteChange(event.target.value)}
          className="mt-2 block min-h-[64px] max-h-[160px] w-full resize-y rounded-[8px] border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-sm leading-6 text-[var(--color-text)] outline-none focus:border-[var(--color-brand)] focus:ring-2 focus:ring-[var(--color-brand-soft)]"
          placeholder="给本草稿的简短备注（不超过 200 字，仅作元数据，不写入正文）"
        />
        <p className="mt-1 text-right text-xs text-[var(--color-text-muted)]">
          {note.length}/{NOTE_MAX_LEN}
        </p>
      </div>
      <div>
        <label htmlFor="draft-tag" className="text-sm font-medium text-[var(--color-text)]">
          标签（可选）
        </label>
        <input
          id="draft-tag"
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

// --- 保存/更新栏（无任何起草/生成入口）------------------------------------------

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
    <section aria-label="保存草稿" className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-3">
        <button
          type="button"
          onClick={onSave}
          disabled={!canSave}
          className="inline-flex h-10 items-center justify-center rounded-[8px] bg-[var(--color-brand)] px-4 text-sm font-semibold text-[var(--color-on-brand,#fff)] transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {status.kind === "saving" ? "保存中…" : editing ? "更新草稿" : "保存草稿"}
        </button>
        <button
          type="button"
          onClick={onReset}
          className="inline-flex h-10 items-center justify-center rounded-[8px] border border-[var(--color-border-strong)] bg-[var(--color-surface)] px-4 text-sm font-medium text-[var(--color-text)] transition hover:bg-[var(--color-surface-muted)]"
        >
          新建空白草稿
        </button>
        {status.kind === "saved" ? (
          <span className="text-xs text-[var(--color-text-muted)]">已保存（仅本人/团队可见）。</span>
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

// --- 已保存草稿列表（仅本人/团队可见，零正文）-----------------------------------

function SavedDrafts({
  drafts,
  listError,
  onLoad,
  onExport,
  exportNotice,
}: {
  drafts: DraftDescriptorView[];
  listError: string | null;
  onLoad: (draft: DraftDescriptorView) => void;
  onExport: (draft: DraftDescriptorView, format: DraftExportFormat) => void;
  exportNotice: { draftId: string; message: string } | null;
}) {
  return (
    <section
      aria-label="已保存草稿"
      className="flex flex-col gap-3 rounded-[10px] border border-[var(--color-border)] bg-[var(--color-surface)] p-5 sm:p-6"
    >
      <p className="text-sm font-medium text-[var(--color-text)]">已保存草稿（仅本人/团队可见）</p>
      <p className="text-xs text-[var(--color-text-muted)]">
        导出文件仅含段落标题、引用元数据与来源锚点、你的备注，并强制附带免责头；不含裁判文书正文，也不含胜负判断或法律结论。
      </p>
      {listError ? (
        <p className="text-xs text-[var(--color-text-muted)]">{listError}</p>
      ) : drafts.length === 0 ? (
        <p className="text-xs text-[var(--color-text-subtle)]">暂无已保存草稿。</p>
      ) : (
        <ul className="grid gap-2">
          {drafts.map((draft) => (
            <li
              key={draft.draft_id}
              className="flex flex-col gap-2 rounded-[8px] border border-[var(--color-border)] bg-[var(--color-surface-muted)] p-3"
            >
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <p className="break-words text-sm text-[var(--color-text)]">
                    {draft.structure_skeleton[0] || "（未命名草稿）"}
                  </p>
                  <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-xs leading-5 text-[var(--color-text-muted)]">
                    <span>{draft.structure_skeleton.length} 个段落标题</span>
                    <span>{draft.candidate_refs.length} 条类案引用</span>
                    <span>{draft.statute_refs.length} 条法条引用</span>
                    {draft.tag ? <span>#{draft.tag}</span> : null}
                  </div>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  <button
                    type="button"
                    aria-label={`导出草稿 Markdown ${draft.draft_id}`}
                    onClick={() => onExport(draft, "markdown")}
                    className="inline-flex h-8 items-center justify-center rounded-[8px] border border-[var(--color-border-strong)] bg-[var(--color-surface)] px-3 text-xs font-medium text-[var(--color-text)] transition hover:bg-[var(--color-surface-muted)]"
                  >
                    导出 Markdown
                  </button>
                  <button
                    type="button"
                    aria-label={`导出草稿 纯文本 ${draft.draft_id}`}
                    onClick={() => onExport(draft, "text")}
                    className="inline-flex h-8 items-center justify-center rounded-[8px] border border-[var(--color-border-strong)] bg-[var(--color-surface)] px-3 text-xs font-medium text-[var(--color-text)] transition hover:bg-[var(--color-surface-muted)]"
                  >
                    导出文本
                  </button>
                  <button
                    type="button"
                    onClick={() => onLoad(draft)}
                    className="inline-flex h-8 items-center justify-center rounded-[8px] border border-[var(--color-border-strong)] bg-[var(--color-surface)] px-3 text-xs font-medium text-[var(--color-brand)] transition hover:bg-[var(--color-brand-soft)]"
                  >
                    载入编辑
                  </button>
                </div>
              </div>
              {exportNotice && exportNotice.draftId === draft.draft_id ? (
                <p role="status" className="text-xs leading-5 text-[var(--color-text-muted)]">
                  {exportNotice.message}
                </p>
              ) : null}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
