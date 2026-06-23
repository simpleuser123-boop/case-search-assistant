import { useState } from "react";
import type { MouseEvent } from "react";

// M4-4 类案清单组装（F17）。一个纯展示型「加入清单」按钮 + 轻量弹层：自身不读写
// storage，可选清单与回调由父组件（SearchPage）统一管理——清单只存引用/元数据/
// 锚点/用户自填短字段，不存正文、不参与主排序。按钮在结果卡片 / 详情抽屉 / 对比
// 视图复用。

// 供选择的清单摘要（仅 id + 标题 + 是否已含本案 + 项数，无正文）。
export type ListChoice = {
  list_id: string;
  list_title: string;
  contains: boolean;
  item_count: number;
};

// 父组件传入的清单选择状态：当前案例在哪些清单内、可加入/移出的回调、新建并加入。
export type ListSelectionState = {
  choices: ListChoice[];
  // 切换某清单是否包含本案（已含则移出，未含则加入）。
  onToggleList: (listId: string) => void;
  // 新建一张清单并把本案作为首项加入。title 为用户自填短字段。
  onCreateAndAdd: (title: string) => void;
  // 本案当前所在清单数量（用于按钮态展示）。
  inCount: number;
};

type AddToListButtonProps = {
  selection: ListSelectionState;
  caseTitle: string;
  size?: "sm" | "md";
  stopPropagation?: boolean;
};

export function AddToListButton({
  selection,
  caseTitle,
  size = "sm",
  stopPropagation = false,
}: AddToListButtonProps) {
  const [open, setOpen] = useState(false);
  const [newTitle, setNewTitle] = useState("");
  const sizeClass = size === "md" ? "h-9 px-3 text-sm" : "h-8 px-2.5 text-xs";
  const active = selection.inCount > 0;

  function stop(event: MouseEvent) {
    if (stopPropagation) {
      event.stopPropagation();
    }
  }

  return (
    <div className="relative inline-flex" onClick={stop}>
      <button
        type="button"
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-label={`加入类案清单：${caseTitle}`}
        title={
          active
            ? `已在 ${selection.inCount} 个清单（仅本地保存元数据与来源引用，不保存正文）`
            : "加入类案清单（仅保存引用与备注，不保存正文）"
        }
        className={[
          "inline-flex shrink-0 items-center justify-center gap-1.5 rounded-[8px] border font-medium transition focus:outline-none focus:ring-2 focus:ring-[var(--color-brand-soft)]",
          sizeClass,
          active
            ? "border-[var(--color-brand)] bg-[var(--color-brand-soft)] text-[var(--color-brand)]"
            : "border-[var(--color-border-strong)] bg-[var(--color-surface)] text-[var(--color-text-muted)] hover:bg-[var(--color-surface-muted)]",
        ].join(" ")}
        onClick={(event) => {
          stop(event);
          setOpen((value) => !value);
        }}
      >
        <span aria-hidden="true">{active ? "✓" : "＋"}</span>
        {active ? `清单（${selection.inCount}）` : "加入清单"}
      </button>

      {open ? (
        <div
          role="dialog"
          aria-label="选择类案清单"
          className="absolute right-0 top-[calc(100%+4px)] z-30 w-64 rounded-[8px] border border-[var(--color-border-strong)] bg-[var(--color-surface)] p-2 text-left shadow-[0_4px_16px_rgba(0,0,0,0.10)]"
        >
          <p className="px-1 pb-1 text-[11px] text-[var(--color-text-muted)]">
            清单只保存引用与备注，不保存裁判正文，仅存于本浏览器、不影响排序。
          </p>

          {selection.choices.length > 0 ? (
            <ul className="max-h-44 space-y-0.5 overflow-auto">
              {selection.choices.map((choice) => (
                <li key={choice.list_id}>
                  <label className="flex cursor-pointer items-center gap-2 rounded-[6px] px-1.5 py-1 text-xs text-[var(--color-text)] hover:bg-[var(--color-surface-muted)]">
                    <input
                      type="checkbox"
                      className="h-3.5 w-3.5 accent-[var(--color-brand)]"
                      checked={choice.contains}
                      aria-label={`${choice.contains ? "从清单移出" : "加入清单"}：${
                        choice.list_title || "未命名清单"
                      }`}
                      onChange={() => selection.onToggleList(choice.list_id)}
                    />
                    <span className="min-w-0 flex-1 truncate">
                      {choice.list_title || "未命名清单"}
                    </span>
                    <span className="shrink-0 text-[10px] text-[var(--color-text-subtle)]">
                      {choice.item_count}
                    </span>
                  </label>
                </li>
              ))}
            </ul>
          ) : (
            <p className="px-1.5 py-1 text-xs text-[var(--color-text-subtle)]">
              还没有清单，新建一个吧。
            </p>
          )}

          <div className="mt-2 flex items-center gap-1.5 border-t border-[var(--color-border)] pt-2">
            <input
              type="text"
              value={newTitle}
              maxLength={40}
              placeholder="新建清单名称"
              className="min-w-0 flex-1 rounded-[6px] border border-[var(--color-border)] bg-[var(--color-bg)] px-2 py-1 text-xs text-[var(--color-text)] outline-none focus:border-[var(--color-brand)] focus:ring-2 focus:ring-[var(--color-brand-soft)]"
              onChange={(event) => setNewTitle(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && newTitle.trim()) {
                  selection.onCreateAndAdd(newTitle);
                  setNewTitle("");
                  setOpen(false);
                }
              }}
            />
            <button
              type="button"
              className="shrink-0 rounded-[6px] bg-[var(--color-brand)] px-2.5 py-1 text-xs font-medium text-white transition hover:bg-[var(--color-brand-hover)] focus:outline-none focus:ring-2 focus:ring-[var(--color-brand-soft)]"
              onClick={() => {
                if (!newTitle.trim()) {
                  return;
                }
                selection.onCreateAndAdd(newTitle);
                setNewTitle("");
                setOpen(false);
              }}
            >
              新建并加入
            </button>
          </div>

          <div className="mt-1.5 flex justify-end">
            <button
              type="button"
              className="rounded-[6px] px-2 py-0.5 text-[11px] text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
              onClick={() => setOpen(false)}
            >
              关闭
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
