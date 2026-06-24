import { Link } from "react-router-dom";

import { isM1M5AcceptanceEnabled } from "../../config/featureFlags";

const CAPABILITIES = [
  {
    stage: "M1-M3",
    title: "可信检索与阅读提效",
    items: ["基础检索", "扩展检索入口", "低置信候选", "结果详情", "案例对比"],
  },
  {
    stage: "M4",
    title: "工作流沉淀",
    items: ["检索历史", "草稿恢复", "案例收藏", "类案清单", "清单导出", "轻量报告"],
  },
  {
    stage: "M5",
    title: "商业化扩展",
    items: ["账号", "团队", "权限", "共享", "批量导入", "计费"],
  },
];

export function M1M5AcceptancePanel() {
  if (!isM1M5AcceptanceEnabled()) {
    return null;
  }

  return (
    <section
      aria-label="M1-M5 本机验收入口"
      className="rounded-[8px] border border-[var(--color-border)] bg-[var(--color-surface)] p-4 text-sm text-[var(--color-text)]"
    >
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <p className="text-xs font-medium text-[var(--color-brand)]">本机验收模式</p>
          <h2 className="mt-1 text-base font-semibold">M1-M5 前端能力总览</h2>
          <p className="mt-1 max-w-3xl text-xs leading-5 text-[var(--color-text-muted)]">
            当前本机已打开 M1-M5 已完成 UI 能力。E 系列多产品入口仍按分步骤关闭，不在此处提前展示。
          </p>
        </div>
        <Link
          to="/search"
          className="inline-flex h-9 shrink-0 items-center justify-center rounded-[6px] border border-[var(--color-border-strong)] px-3 text-xs font-medium text-[var(--color-text)] hover:bg-[var(--color-surface-muted)]"
        >
          查看结果页能力
        </Link>
      </div>

      <div className="mt-4 grid gap-3 lg:grid-cols-3">
        {CAPABILITIES.map((group) => (
          <div
            key={group.stage}
            className="rounded-[6px] border border-[var(--color-border)] bg-[var(--color-bg)] px-3 py-2"
          >
            <div className="flex items-center justify-between gap-2">
              <span className="text-xs font-medium text-[var(--color-brand)]">{group.stage}</span>
              <span className="text-[11px] text-[var(--color-text-muted)]">已打开</span>
            </div>
            <p className="mt-1 font-medium">{group.title}</p>
            <div className="mt-2 flex flex-wrap gap-1.5">
              {group.items.map((item) => (
                <span
                  key={item}
                  className="rounded-[4px] border border-[var(--color-border)] bg-[var(--color-surface)] px-2 py-0.5 text-[11px] text-[var(--color-text-muted)]"
                >
                  {item}
                </span>
              ))}
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}
