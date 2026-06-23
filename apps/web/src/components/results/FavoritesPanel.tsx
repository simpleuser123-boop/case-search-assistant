import { useState } from "react";

import type { CaseFavoriteRecord } from "../../lib/caseFavorite";

// M4-3 收藏列表（F17）。只展示元数据 + 用户自填短字段，提供回跳详情、编辑
// note/tag、取消单条、清空全部入口。组件不直接读写 storage，也不向后端发送
// 任何正文——所有持久化由父组件经 caseFavorite 纯函数完成。收藏不参与主排序。

type FavoritesPanelProps = {
  favorites: CaseFavoriteRecord[];
  onOpenDetail: (record: CaseFavoriteRecord) => void;
  onRemove: (caseId: string) => void;
  onClearAll: () => void;
  onUpdateFields: (caseId: string, fields: { note?: string; tag?: string }) => void;
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

export function FavoritesPanel({
  favorites,
  onOpenDetail,
  onRemove,
  onClearAll,
  onUpdateFields,
}: FavoritesPanelProps) {
  const [confirmingClear, setConfirmingClear] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);

  if (favorites.length === 0) {
    return null;
  }

  return (
    <section
      aria-label="案例收藏"
      className="rounded-[8px] border border-[var(--color-border)] bg-[var(--color-surface)] p-4 text-sm leading-6"
    >
      <div className="flex items-center justify-between gap-3">
        <h2 className="font-semibold text-[var(--color-text)]">案例收藏（{favorites.length}）</h2>
        {confirmingClear ? (
          <span className="flex items-center gap-2 text-xs">
            <span className="text-[var(--color-text-muted)]">确认清空？</span>
            <button
              type="button"
              className="rounded-[6px] border border-[var(--color-danger)] px-2 py-0.5 font-medium text-[var(--color-danger)] transition hover:bg-[var(--color-danger-soft,#fde8e8)] focus:outline-none focus:ring-2 focus:ring-[var(--color-brand-soft)]"
              onClick={() => {
                onClearAll();
                setConfirmingClear(false);
              }}
            >
              清空全部
            </button>
            <button
              type="button"
              className="rounded-[6px] border border-[var(--color-border-strong)] px-2 py-0.5 text-[var(--color-text-muted)] transition hover:bg-[var(--color-surface-muted)] focus:outline-none focus:ring-2 focus:ring-[var(--color-brand-soft)]"
              onClick={() => setConfirmingClear(false)}
            >
              取消
            </button>
          </span>
        ) : (
          <button
            type="button"
            className="rounded-[6px] border border-[var(--color-border-strong)] px-2.5 py-1 text-xs font-medium text-[var(--color-text-muted)] transition hover:bg-[var(--color-surface-muted)] focus:outline-none focus:ring-2 focus:ring-[var(--color-brand-soft)]"
            onClick={() => setConfirmingClear(true)}
          >
            清空收藏
          </button>
        )}
      </div>

      <p className="mt-1 text-xs text-[var(--color-text-muted)]">
        收藏只保存案号等元数据与来源引用，不保存裁判正文；仅存于本浏览器、可随时清除，不上送服务器，也不影响排序。
      </p>

      <ul className="mt-3 space-y-2">
        {favorites.map((record) => {
          const title = record.case_number || "案号暂缺";
          const metaLine = [
            record.court,
            record.trial_level,
            record.case_cause,
            record.judgment_date,
          ]
            .filter(Boolean)
            .join(" · ");
          const isEditing = editingId === record.case_id;
          return (
            <li
              key={record.case_id}
              className="rounded-[6px] border border-[var(--color-border)] bg-[var(--color-bg)] px-3 py-2"
            >
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="truncate text-sm font-medium text-[var(--color-text)]">{title}</p>
                  {metaLine ? (
                    <p className="mt-0.5 truncate text-xs text-[var(--color-text-muted)]">{metaLine}</p>
                  ) : null}
                  <p className="mt-0.5 text-[11px] text-[var(--color-text-subtle)]">
                    收藏于 {formatTimestamp(record.created_at)}
                    {record.tag ? ` · 标签：${record.tag}` : ""}
                  </p>
                  {record.note ? (
                    <p className="mt-1 break-words text-xs text-[var(--color-text)]">备注：{record.note}</p>
                  ) : null}
                </div>
                <div className="flex shrink-0 items-center gap-1.5">
                  <button
                    type="button"
                    className="rounded-[6px] bg-[var(--color-brand)] px-2.5 py-1 text-xs font-medium text-white transition hover:bg-[var(--color-brand-hover)] focus:outline-none focus:ring-2 focus:ring-[var(--color-brand-soft)]"
                    onClick={() => onOpenDetail(record)}
                  >
                    查看详情
                  </button>
                  <button
                    type="button"
                    className="rounded-[6px] border border-[var(--color-border-strong)] px-2 py-1 text-xs text-[var(--color-text-muted)] transition hover:bg-[var(--color-surface-muted)] focus:outline-none focus:ring-2 focus:ring-[var(--color-brand-soft)]"
                    onClick={() => setEditingId(isEditing ? null : record.case_id)}
                  >
                    {isEditing ? "收起" : "备注"}
                  </button>
                  <button
                    type="button"
                    aria-label={`取消收藏：${title}`}
                    className="rounded-[6px] border border-[var(--color-border-strong)] px-2 py-1 text-xs text-[var(--color-text-muted)] transition hover:bg-[var(--color-surface-muted)] focus:outline-none focus:ring-2 focus:ring-[var(--color-brand-soft)]"
                    onClick={() => onRemove(record.case_id)}
                  >
                    取消收藏
                  </button>
                </div>
              </div>

              {isEditing ? (
                <FavoriteFieldEditor
                  note={record.note}
                  tag={record.tag}
                  onSave={(fields) => {
                    onUpdateFields(record.case_id, fields);
                    setEditingId(null);
                  }}
                  onCancel={() => setEditingId(null)}
                />
              ) : null}
            </li>
          );
        })}
      </ul>
    </section>
  );
}

function FavoriteFieldEditor({
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
          className="rounded-[6px] border border-[var(--color-border-strong)] px-2.5 py-1 text-xs text-[var(--color-text-muted)] transition hover:bg-[var(--color-surface)] focus:outline-none focus:ring-2 focus:ring-[var(--color-brand-soft)]"
          onClick={onCancel}
        >
          取消
        </button>
        <button
          type="button"
          className="rounded-[6px] bg-[var(--color-brand)] px-2.5 py-1 text-xs font-medium text-white transition hover:bg-[var(--color-brand-hover)] focus:outline-none focus:ring-2 focus:ring-[var(--color-brand-soft)]"
          onClick={() => onSave({ note: draftNote, tag: draftTag })}
        >
          保存
        </button>
      </div>
    </div>
  );
}
