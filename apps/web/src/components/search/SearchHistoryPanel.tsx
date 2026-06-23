import { useState } from "react";

import type { SearchHistoryEntry } from "../../lib/searchHistory";

// M4-2 检索历史与草稿恢复（F16）。本组件只渲染本地历史条目，提供「重搜」「删除单条」
// 「清空历史」入口。重搜把历史正文交还父组件，由父组件走与首次检索完全相同的清洗 /
// 改写降级 / 主排序默认链路——历史本身不参与、不改变主排序。组件不直接读写 storage，
// 也不向后端发送任何正文。

type SearchHistoryPanelProps = {
  entries: SearchHistoryEntry[];
  draftRestored: boolean;
  onResearch: (entry: SearchHistoryEntry) => void;
  onRemoveEntry: (id: string) => void;
  onClearHistory: () => void;
  onClearDraft: () => void;
  hasDraft: boolean;
};

function formatTimestamp(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) {
    return "";
  }
  const pad = (value: number) => value.toString().padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(
    date.getDate()
  )} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

export function SearchHistoryPanel({
  entries,
  draftRestored,
  onResearch,
  onRemoveEntry,
  onClearHistory,
  onClearDraft,
  hasDraft,
}: SearchHistoryPanelProps) {
  const [confirmingClear, setConfirmingClear] = useState(false);

  const hasHistory = entries.length > 0;
  if (!hasHistory && !hasDraft) {
    return null;
  }

  return (
    <section
      aria-label="检索历史与草稿"
      className="rounded-[8px] border border-[var(--color-border)] bg-[var(--color-surface)] p-4 text-sm leading-6"
    >
      <div className="flex items-center justify-between gap-3">
        <h2 className="font-semibold text-[var(--color-text)]">检索历史</h2>
        {hasHistory ? (
          confirmingClear ? (
            <span className="flex items-center gap-2 text-xs">
              <span className="text-[var(--color-text-muted)]">确认清空？</span>
              <button
                type="button"
                className="rounded-[6px] border border-[var(--color-danger)] px-2 py-0.5 font-medium text-[var(--color-danger)] transition hover:bg-[var(--color-danger-soft,#fde8e8)] focus:outline-none focus:ring-2 focus:ring-[var(--color-brand-soft)]"
                onClick={() => {
                  onClearHistory();
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
              清除历史
            </button>
          )
        ) : null}
      </div>

      <p className="mt-1 text-xs text-[var(--color-text-muted)]">
        历史与草稿只保存在本浏览器、可随时清除，不会上送服务器。
      </p>

      {hasDraft ? (
        <div className="mt-3 rounded-[6px] border border-dashed border-[var(--color-border-strong)] bg-[var(--color-surface-muted)] px-3 py-2">
          <div className="flex items-center justify-between gap-2">
            <p className="text-xs font-medium text-[var(--color-text)]">
              {draftRestored ? "已恢复上次未提交的草稿" : "已保存当前输入草稿"}
            </p>
            <button
              type="button"
              className="rounded-[6px] border border-[var(--color-border-strong)] px-2 py-0.5 text-xs text-[var(--color-text-muted)] transition hover:bg-[var(--color-surface)] focus:outline-none focus:ring-2 focus:ring-[var(--color-brand-soft)]"
              onClick={onClearDraft}
            >
              清除草稿
            </button>
          </div>
        </div>
      ) : null}

      {hasHistory ? (
        <ul className="mt-3 space-y-2">
          {entries.map((entry) => (
            <li
              key={entry.id}
              className="rounded-[6px] border border-[var(--color-border)] bg-[var(--color-bg)] px-3 py-2"
            >
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="truncate text-sm text-[var(--color-text)]">
                    {entry.title || entry.query_preview}
                  </p>
                  <p className="mt-0.5 text-xs text-[var(--color-text-muted)]">
                    {formatTimestamp(entry.created_at)} · {entry.result_count} 条结果
                    {entry.degraded ? " · 已降级" : ""}
                  </p>
                </div>
                <div className="flex shrink-0 items-center gap-1.5">
                  <button
                    type="button"
                    className="rounded-[6px] bg-[var(--color-brand)] px-2.5 py-1 text-xs font-medium text-white transition hover:bg-[var(--color-brand-hover)] focus:outline-none focus:ring-2 focus:ring-[var(--color-brand-soft)]"
                    onClick={() => onResearch(entry)}
                  >
                    重搜
                  </button>
                  <button
                    type="button"
                    aria-label={`删除历史：${entry.query_preview}`}
                    className="rounded-[6px] border border-[var(--color-border-strong)] px-2 py-1 text-xs text-[var(--color-text-muted)] transition hover:bg-[var(--color-surface-muted)] focus:outline-none focus:ring-2 focus:ring-[var(--color-brand-soft)]"
                    onClick={() => onRemoveEntry(entry.id)}
                  >
                    删除
                  </button>
                </div>
              </div>
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}
