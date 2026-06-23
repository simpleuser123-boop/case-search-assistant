import type { MouseEvent } from "react";

// M4-3 案例收藏能力（F17）。一个纯展示型收藏切换按钮：自身不读写 storage，
// 收藏态与回调由父组件（SearchPage）统一管理——收藏只存元数据/锚点/短字段，
// 不存正文、不参与主排序。按钮放在结果卡片 / 详情抽屉 / 对比视图复用。

type FavoriteButtonProps = {
  favorited: boolean;
  onToggle: () => void;
  // 用于无障碍标签的案例标题（仅用于 aria-label，不写入收藏存储）。
  caseTitle: string;
  size?: "sm" | "md";
  // 是否阻止冒泡（结果卡片整卡可点开详情时需要）。
  stopPropagation?: boolean;
};

export function FavoriteButton({
  favorited,
  onToggle,
  caseTitle,
  size = "sm",
  stopPropagation = false,
}: FavoriteButtonProps) {
  const sizeClass =
    size === "md" ? "h-9 px-3 text-sm" : "h-8 px-2.5 text-xs";

  return (
    <button
      type="button"
      aria-pressed={favorited}
      aria-label={`${favorited ? "取消收藏" : "收藏"}：${caseTitle}`}
      title={favorited ? "取消收藏（仅本地保存，可清除）" : "收藏（仅保存元数据与来源引用，不保存正文）"}
      className={[
        "inline-flex shrink-0 items-center justify-center gap-1.5 rounded-[8px] border font-medium transition focus:outline-none focus:ring-2 focus:ring-[var(--color-brand-soft)]",
        sizeClass,
        favorited
          ? "border-[var(--color-brand)] bg-[var(--color-brand-soft)] text-[var(--color-brand)]"
          : "border-[var(--color-border-strong)] bg-[var(--color-surface)] text-[var(--color-text-muted)] hover:bg-[var(--color-surface-muted)]",
      ].join(" ")}
      onClick={(event: MouseEvent<HTMLButtonElement>) => {
        if (stopPropagation) {
          event.stopPropagation();
        }
        onToggle();
      }}
    >
      <span aria-hidden="true">{favorited ? "★" : "☆"}</span>
      {favorited ? "已收藏" : "收藏"}
    </button>
  );
}
