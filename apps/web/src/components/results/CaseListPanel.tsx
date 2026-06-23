import { useState } from "react";

import type { CaseListItem, CaseListRecord } from "../../lib/caseList";
import type { ExportFormat } from "../../lib/caseListExport";
import type { ReportTemplate } from "../../lib/reportTemplate";
import { ReportPreview } from "./ReportPreview";

// M4-4 类案清单面板（F17）。只展示引用 + 元数据 + 用户自填短字段，提供：手动排序
// （上移/下移，仅影响清单展示，不回写主排序）、回跳详情、编辑 note/tag、删除项、
// 重命名清单、删除整张清单。组件不直接读写 storage，也不向后端发送任何正文——
// 所有持久化由父组件经 caseList 纯函数完成。清单不参与主排序/召回/source selection。

type CaseListPanelProps = {
  lists: CaseListRecord[];
  onOpenDetail: (item: CaseListItem) => void;
  onRemoveItem: (listId: string, caseId: string) => void;
  onMoveItem: (listId: string, caseId: string, direction: "up" | "down") => void;
  onUpdateItemFields: (
    listId: string,
    caseId: string,
    fields: { note?: string; tag?: string }
  ) => void;
  onRenameList: (listId: string, title: string) => void;
  onDeleteList: (listId: string) => void;
  // M4-5 导出：flag 关闭时为 false / undefined，面板不渲染任何导出入口。
  exportEnabled?: boolean;
  // 返回导出结果状态，供面板展示成功 / 降级提示。
  onExportList?: (
    listId: string,
    format: ExportFormat
  ) => { ok: boolean; status: string };
  // M4-6 报告：flag 关闭时为 false / undefined，面板不渲染任何报告入口。
  reportEnabled?: boolean;
  // onGenerateReport: 基于清单组装报告骨架（可带检索背景备注），返回报告供预览。
  onGenerateReport?: (listId: string, backgroundNote: string) => ReportTemplate;
  // onDownloadReport: 导出指定报告为 Markdown 文件，返回状态供展示成功 / 降级提示。
  onDownloadReport?: (report: ReportTemplate) => { ok: boolean; status: string };
};

function formatTimestamp(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) {
    return "";
  }
  const pad = (value: number) => value.toString().padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(
    date.getHours()
  )}:${pad(date.getMinutes())}`;
}

export function CaseListPanel({
  lists,
  onOpenDetail,
  onRemoveItem,
  onMoveItem,
  onUpdateItemFields,
  onRenameList,
  onDeleteList,
  exportEnabled,
  onExportList,
  reportEnabled,
  onGenerateReport,
  onDownloadReport,
}: CaseListPanelProps) {
  if (lists.length === 0) {
    return null;
  }

  return (
    <section
      aria-label="类案清单"
      className="rounded-[8px] border border-[var(--color-border)] bg-[var(--color-surface)] p-4 text-sm leading-6"
    >
      <h2 className="font-semibold text-[var(--color-text)]">类案清单（{lists.length}）</h2>
      <p className="mt-1 text-xs text-[var(--color-text-muted)]">
        清单只保存案号等元数据、来源引用与你的备注，不保存裁判正文；仅存于本浏览器、可随时清除，不上送服务器。手动排序只影响清单展示，不改变搜索结果排序。
      </p>

      <div className="mt-3 space-y-3">
        {lists.map((list) => (
          <CaseListCard
            key={list.list_id}
            list={list}
            onOpenDetail={onOpenDetail}
            onRemoveItem={onRemoveItem}
            onMoveItem={onMoveItem}
            onUpdateItemFields={onUpdateItemFields}
            onRenameList={onRenameList}
            onDeleteList={onDeleteList}
            exportEnabled={exportEnabled}
            onExportList={onExportList}
            reportEnabled={reportEnabled}
            onGenerateReport={onGenerateReport}
            onDownloadReport={onDownloadReport}
          />
        ))}
      </div>
    </section>
  );
}

function CaseListCard({
  list,
  onOpenDetail,
  onRemoveItem,
  onMoveItem,
  onUpdateItemFields,
  onRenameList,
  onDeleteList,
  exportEnabled,
  onExportList,
  reportEnabled,
  onGenerateReport,
  onDownloadReport,
}: {
  list: CaseListRecord;
  onOpenDetail: (item: CaseListItem) => void;
  onRemoveItem: (listId: string, caseId: string) => void;
  onMoveItem: (listId: string, caseId: string, direction: "up" | "down") => void;
  onUpdateItemFields: (
    listId: string,
    caseId: string,
    fields: { note?: string; tag?: string }
  ) => void;
  onRenameList: (listId: string, title: string) => void;
  onDeleteList: (listId: string) => void;
  exportEnabled?: boolean;
  onExportList?: (
    listId: string,
    format: ExportFormat
  ) => { ok: boolean; status: string };
  reportEnabled?: boolean;
  onGenerateReport?: (listId: string, backgroundNote: string) => ReportTemplate;
  onDownloadReport?: (report: ReportTemplate) => { ok: boolean; status: string };
}) {
  const [editingTitle, setEditingTitle] = useState(false);
  const [titleDraft, setTitleDraft] = useState(list.list_title);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [editingItemId, setEditingItemId] = useState<string | null>(null);
  const [reportOpen, setReportOpen] = useState(false);
  const [report, setReport] = useState<ReportTemplate | null>(null);

  const title = list.list_title || "未命名清单";

  return (
    <div className="rounded-[6px] border border-[var(--color-border)] bg-[var(--color-bg)] p-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          {editingTitle ? (
            <div className="flex items-center gap-1.5">
              <input
                type="text"
                value={titleDraft}
                maxLength={40}
                aria-label="清单名称"
                className="min-w-0 flex-1 rounded-[6px] border border-[var(--color-border)] bg-[var(--color-surface)] px-2 py-1 text-sm text-[var(--color-text)] outline-none focus:border-[var(--color-brand)] focus:ring-2 focus:ring-[var(--color-brand-soft)]"
                onChange={(event) => setTitleDraft(event.target.value)}
              />
              <button
                type="button"
                className="rounded-[6px] bg-[var(--color-brand)] px-2 py-1 text-xs font-medium text-white hover:bg-[var(--color-brand-hover)] focus:outline-none focus:ring-2 focus:ring-[var(--color-brand-soft)]"
                onClick={() => {
                  onRenameList(list.list_id, titleDraft);
                  setEditingTitle(false);
                }}
              >
                保存
              </button>
            </div>
          ) : (
            <button
              type="button"
              className="truncate text-sm font-medium text-[var(--color-text)] hover:underline"
              title="点击重命名清单"
              onClick={() => {
                setTitleDraft(list.list_title);
                setEditingTitle(true);
              }}
            >
              {title}（{list.items.length}）
            </button>
          )}
          <p className="mt-0.5 text-[11px] text-[var(--color-text-subtle)]">
            更新于 {formatTimestamp(list.updated_at)}
          </p>
        </div>

        <div className="flex shrink-0 items-center gap-1.5">
          {confirmingDelete ? (
            <>
              <button
                type="button"
                className="rounded-[6px] border border-[var(--color-danger)] px-2 py-0.5 text-xs font-medium text-[var(--color-danger)] hover:bg-[var(--color-danger-soft,#fde8e8)] focus:outline-none focus:ring-2 focus:ring-[var(--color-brand-soft)]"
                onClick={() => {
                  onDeleteList(list.list_id);
                  setConfirmingDelete(false);
                }}
              >
                删除清单
              </button>
              <button
                type="button"
                className="rounded-[6px] border border-[var(--color-border-strong)] px-2 py-0.5 text-xs text-[var(--color-text-muted)] hover:bg-[var(--color-surface-muted)] focus:outline-none focus:ring-2 focus:ring-[var(--color-brand-soft)]"
                onClick={() => setConfirmingDelete(false)}
              >
                取消
              </button>
            </>
          ) : (
            <button
              type="button"
              aria-label={`删除清单：${title}`}
              className="rounded-[6px] border border-[var(--color-border-strong)] px-2 py-0.5 text-xs text-[var(--color-text-muted)] hover:bg-[var(--color-surface-muted)] focus:outline-none focus:ring-2 focus:ring-[var(--color-brand-soft)]"
              onClick={() => setConfirmingDelete(true)}
            >
              删除清单
            </button>
          )}
        </div>
      </div>

      {list.items.length === 0 ? (
        <p className="mt-2 text-xs text-[var(--color-text-subtle)]">清单暂无案例。</p>
      ) : (
        <ol className="mt-2 space-y-1.5">
          {list.items.map((item, index) => (
            <CaseListItemRow
              key={item.case_id}
              listId={list.list_id}
              item={item}
              index={index}
              total={list.items.length}
              editing={editingItemId === item.case_id}
              onOpenDetail={onOpenDetail}
              onRemoveItem={onRemoveItem}
              onMoveItem={onMoveItem}
              onToggleEditing={() =>
                setEditingItemId(editingItemId === item.case_id ? null : item.case_id)
              }
              onSaveFields={(fields) => {
                onUpdateItemFields(list.list_id, item.case_id, fields);
                setEditingItemId(null);
              }}
            />
          ))}
        </ol>
      )}

      {exportEnabled && onExportList && list.items.length > 0 ? (
        <ExportControl
          onExport={(format) => onExportList(list.list_id, format)}
        />
      ) : null}

      {reportEnabled && onGenerateReport && onDownloadReport && list.items.length > 0 ? (
        <div className="mt-3 border-t border-dashed border-[var(--color-border)] pt-2.5">
          {reportOpen ? (
            <ReportPreview
              report={report}
              onGenerate={(backgroundNote) => {
                const next = onGenerateReport(list.list_id, backgroundNote);
                setReport(next);
              }}
              onDownload={() => {
                if (!report) {
                  return { ok: false, status: "failed" };
                }
                return onDownloadReport(report);
              }}
              onClose={() => setReportOpen(false)}
            />
          ) : (
            <button
              type="button"
              className="rounded-[6px] border border-[var(--color-border-strong)] px-2 py-0.5 text-[11px] text-[var(--color-text)] hover:bg-[var(--color-surface-muted)] focus:outline-none focus:ring-2 focus:ring-[var(--color-brand-soft)]"
              onClick={() => setReportOpen(true)}
            >
              生成类案报告模板
            </button>
          )}
        </div>
      ) : null}
    </div>
  );
}

function CaseListItemRow({
  listId,
  item,
  index,
  total,
  editing,
  onOpenDetail,
  onRemoveItem,
  onMoveItem,
  onToggleEditing,
  onSaveFields,
}: {
  listId: string;
  item: CaseListItem;
  index: number;
  total: number;
  editing: boolean;
  onOpenDetail: (item: CaseListItem) => void;
  onRemoveItem: (listId: string, caseId: string) => void;
  onMoveItem: (listId: string, caseId: string, direction: "up" | "down") => void;
  onToggleEditing: () => void;
  onSaveFields: (fields: { note: string; tag: string }) => void;
}) {
  const title = item.case_number || "案号暂缺";
  const metaLine = [item.court, item.trial_level, item.case_cause, item.judgment_date]
    .filter(Boolean)
    .join(" · ");
  const hasAnchor = item.source_anchors.length > 0;

  return (
    <li className="rounded-[6px] border border-[var(--color-border)] bg-[var(--color-surface)] px-2.5 py-2">
      <div className="flex items-start justify-between gap-2">
        <div className="flex min-w-0 items-start gap-2">
          <div className="flex shrink-0 flex-col">
            <button
              type="button"
              aria-label={`上移：${title}`}
              disabled={index === 0}
              className="leading-none text-[var(--color-text-muted)] hover:text-[var(--color-text)] disabled:cursor-not-allowed disabled:opacity-30"
              onClick={() => onMoveItem(listId, item.case_id, "up")}
            >
              ▲
            </button>
            <button
              type="button"
              aria-label={`下移：${title}`}
              disabled={index === total - 1}
              className="leading-none text-[var(--color-text-muted)] hover:text-[var(--color-text)] disabled:cursor-not-allowed disabled:opacity-30"
              onClick={() => onMoveItem(listId, item.case_id, "down")}
            >
              ▼
            </button>
          </div>
          <div className="min-w-0">
            <p className="truncate text-xs font-medium text-[var(--color-text)]">
              {index + 1}. {title}
            </p>
            {metaLine ? (
              <p className="mt-0.5 truncate text-[11px] text-[var(--color-text-muted)]">{metaLine}</p>
            ) : null}
            {hasAnchor ? (
              <p className="mt-0.5 truncate font-mono text-[10px] text-[var(--color-text-subtle)]">
                来源 {item.source_anchors.map((a) => a.source_chunk_id).join(", ")}
              </p>
            ) : (
              <p className="mt-0.5 text-[10px] text-[var(--color-text-subtle)]">来源引用暂缺</p>
            )}
            {item.tag ? (
              <span className="mt-1 inline-flex rounded-[4px] bg-[var(--color-surface-muted)] px-1.5 py-0.5 text-[10px] text-[var(--color-text-muted)]">
                {item.tag}
              </span>
            ) : null}
            {item.note ? (
              <p className="mt-1 break-words text-[11px] text-[var(--color-text)]">备注：{item.note}</p>
            ) : null}
          </div>
        </div>

        <div className="flex shrink-0 items-center gap-1">
          <button
            type="button"
            className="rounded-[6px] bg-[var(--color-brand)] px-2 py-0.5 text-[11px] font-medium text-white hover:bg-[var(--color-brand-hover)] focus:outline-none focus:ring-2 focus:ring-[var(--color-brand-soft)]"
            onClick={() => onOpenDetail(item)}
          >
            详情
          </button>
          <button
            type="button"
            className="rounded-[6px] border border-[var(--color-border-strong)] px-1.5 py-0.5 text-[11px] text-[var(--color-text-muted)] hover:bg-[var(--color-surface-muted)] focus:outline-none focus:ring-2 focus:ring-[var(--color-brand-soft)]"
            onClick={onToggleEditing}
          >
            {editing ? "收起" : "备注"}
          </button>
          <button
            type="button"
            aria-label={`从清单移除：${title}`}
            className="rounded-[6px] border border-[var(--color-border-strong)] px-1.5 py-0.5 text-[11px] text-[var(--color-text-muted)] hover:bg-[var(--color-surface-muted)] focus:outline-none focus:ring-2 focus:ring-[var(--color-brand-soft)]"
            onClick={() => onRemoveItem(listId, item.case_id)}
          >
            移除
          </button>
        </div>
      </div>

      {editing ? (
        <ListItemFieldEditor
          note={item.note}
          tag={item.tag}
          onSave={onSaveFields}
          onCancel={onToggleEditing}
        />
      ) : null}
    </li>
  );
}

function ListItemFieldEditor({
  note,
  tag,
  onSave,
  onCancel,
}: {
  note: string;
  tag: string;
  onSave: (fields: { note: string; tag: string }) => void;
  onCancel: () => void;
}) {
  const [draftNote, setDraftNote] = useState(note);
  const [draftTag, setDraftTag] = useState(tag);

  return (
    <div className="mt-2 space-y-2 rounded-[6px] border border-dashed border-[var(--color-border-strong)] bg-[var(--color-surface-muted)] p-2">
      <label className="block text-[11px] text-[var(--color-text-muted)]">
        标签（短）
        <input
          type="text"
          value={draftTag}
          maxLength={24}
          className="mt-1 block w-full rounded-[6px] border border-[var(--color-border)] bg-[var(--color-surface)] px-2 py-1 text-xs text-[var(--color-text)] outline-none focus:border-[var(--color-brand)] focus:ring-2 focus:ring-[var(--color-brand-soft)]"
          onChange={(event) => setDraftTag(event.target.value)}
        />
      </label>
      <label className="block text-[11px] text-[var(--color-text-muted)]">
        备注（短）
        <textarea
          value={draftNote}
          maxLength={120}
          rows={2}
          className="mt-1 block w-full resize-none rounded-[6px] border border-[var(--color-border)] bg-[var(--color-surface)] px-2 py-1 text-xs text-[var(--color-text)] outline-none focus:border-[var(--color-brand)] focus:ring-2 focus:ring-[var(--color-brand-soft)]"
          onChange={(event) => setDraftNote(event.target.value)}
        />
      </label>
      <div className="flex justify-end gap-2">
        <button
          type="button"
          className="rounded-[6px] border border-[var(--color-border-strong)] px-2.5 py-1 text-xs text-[var(--color-text-muted)] hover:bg-[var(--color-surface)] focus:outline-none focus:ring-2 focus:ring-[var(--color-brand-soft)]"
          onClick={onCancel}
        >
          取消
        </button>
        <button
          type="button"
          className="rounded-[6px] bg-[var(--color-brand)] px-2.5 py-1 text-xs font-medium text-white hover:bg-[var(--color-brand-hover)] focus:outline-none focus:ring-2 focus:ring-[var(--color-brand-soft)]"
          onClick={() => onSave({ note: draftNote, tag: draftTag })}
        >
          保存
        </button>
      </div>
    </div>
  );
}

// M4-5 导出控件：选择格式（Markdown / CSV）触发本地下载。导出只生成元数据 / 来源
// 引用 / 用户自填备注 + 免责说明的文件，绝不含正文。导出失败给出安全提示，不影响
// 主链路。组件本身不生成文件内容——交由父组件经 caseListExport 纯函数完成。
function ExportControl({
  onExport,
}: {
  onExport: (format: ExportFormat) => { ok: boolean; status: string };
}) {
  const [feedback, setFeedback] = useState<{ ok: boolean; message: string } | null>(null);

  function runExport(format: ExportFormat) {
    let result: { ok: boolean; status: string };
    try {
      result = onExport(format);
    } catch {
      setFeedback({ ok: false, message: "导出未完成，请稍后重试（不影响检索与清单）。" });
      return;
    }
    if (result.ok) {
      setFeedback({ ok: true, message: "已生成导出文件（仅含元数据、来源引用与你的备注）。" });
    } else if (result.status === "degraded") {
      setFeedback({
        ok: false,
        message: "当前环境无法自动下载，已安全跳过；可换用其他浏览器重试，不影响检索与清单。",
      });
    } else if (result.status === "empty") {
      setFeedback({ ok: false, message: "清单暂无可导出的案例。" });
    } else {
      setFeedback({ ok: false, message: "导出未完成，请稍后重试（不影响检索与清单）。" });
    }
  }

  return (
    <div className="mt-3 border-t border-dashed border-[var(--color-border)] pt-2.5">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-[11px] text-[var(--color-text-muted)]">导出清单：</span>
        <button
          type="button"
          aria-label="导出为 Markdown"
          className="rounded-[6px] border border-[var(--color-border-strong)] px-2 py-0.5 text-[11px] text-[var(--color-text)] hover:bg-[var(--color-surface-muted)] focus:outline-none focus:ring-2 focus:ring-[var(--color-brand-soft)]"
          onClick={() => runExport("markdown")}
        >
          Markdown
        </button>
        <button
          type="button"
          aria-label="导出为 CSV"
          className="rounded-[6px] border border-[var(--color-border-strong)] px-2 py-0.5 text-[11px] text-[var(--color-text)] hover:bg-[var(--color-surface-muted)] focus:outline-none focus:ring-2 focus:ring-[var(--color-brand-soft)]"
          onClick={() => runExport("csv")}
        >
          CSV
        </button>
      </div>
      <p className="mt-1.5 text-[10px] leading-4 text-[var(--color-text-subtle)]">
        导出文件只含案号、法院、审级、案由、裁判日期、来源引用与你的备注，不含裁判文书正文，并附数据覆盖与免责说明，需人工复核。
      </p>
      {feedback ? (
        <p
          role="status"
          className={`mt-1 text-[11px] ${
            feedback.ok ? "text-[var(--color-text-muted)]" : "text-[var(--color-danger)]"
          }`}
        >
          {feedback.message}
        </p>
      ) : null}
    </div>
  );
}
