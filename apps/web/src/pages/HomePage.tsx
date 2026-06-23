import { Link, useNavigate } from "react-router-dom";

import {
  SearchComposer,
  type SearchComposerSubmitMeta,
} from "../components/search/SearchComposer";
import { isCasebookEnabled, isDraftingEnabled, isIntakeEnabled, isStatuteSearchEnabled } from "../config/featureFlags";

export function HomePage() {
  const navigate = useNavigate();
  // E4-4：录入端入口严格受 VITE_ENABLE_INTAKE 门控；默认 off 时不渲染该入口。
  const intakeEnabled = isIntakeEnabled();
  // E5-5：法条检索入口严格受 VITE_ENABLE_STATUTE_SEARCH 门控；默认 off 时不渲染该入口。
  // 与 intake / M1-M5 验收开关正交，互不联动。
  const statuteEnabled = isStatuteSearchEnabled();
  // E6-3：文书工作台入口严格受 VITE_ENABLE_DRAFTING 门控；默认 off 时不渲染该入口。
  // 与 intake / statute / M1-M5 验收开关正交，互不联动。
  const draftingEnabled = isDraftingEnabled();
  // E7-3：案件协作工作台入口严格受 VITE_ENABLE_CASEBOOK 门控；默认 off 时不渲染该入口。
  // 与 intake / statute / drafting / M1-M5 验收开关正交，互不联动。
  const casebookEnabled = isCasebookEnabled();

  function handleSearchSubmit(_query: string, meta: SearchComposerSubmitMeta) {
    navigate("/search", {
      state: {
        query: _query,
        inputLength: meta.inputLength,
      },
    });
  }

  return (
    <main className="min-h-[100dvh] bg-[var(--color-bg)] px-4 py-6 text-[var(--color-text)] sm:px-6 sm:py-10">
      <div className="mx-auto flex w-full max-w-[1180px] flex-col gap-10">
        <header className="flex items-center justify-between border-b border-[var(--color-border)] pb-4">
          <div>
            <p className="text-base font-semibold text-[var(--color-text)]">
              类案检索助手
            </p>
            <p className="mt-1 text-xs text-[var(--color-text-muted)]">
              事实相似度优先的案例检索工作台
            </p>
          </div>
          <div className="flex items-center gap-2">
            {intakeEnabled ? (
              <Link
                to="/intake"
                className="inline-flex rounded-[8px] border border-[var(--color-border-strong)] px-3 py-1.5 text-xs font-medium text-[var(--color-brand)] transition hover:bg-[var(--color-brand-soft)]"
              >
                案情录入端
              </Link>
            ) : null}
            {statuteEnabled ? (
              <Link
                to="/statute"
                className="inline-flex rounded-[8px] border border-[var(--color-border-strong)] px-3 py-1.5 text-xs font-medium text-[var(--color-brand)] transition hover:bg-[var(--color-brand-soft)]"
              >
                法条检索
              </Link>
            ) : null}
            {draftingEnabled ? (
              <Link
                to="/drafting"
                className="inline-flex rounded-[8px] border border-[var(--color-border-strong)] px-3 py-1.5 text-xs font-medium text-[var(--color-brand)] transition hover:bg-[var(--color-brand-soft)]"
              >
                文书工作台
              </Link>
            ) : null}
            {casebookEnabled ? (
              <Link
                to="/casebook"
                className="inline-flex rounded-[8px] border border-[var(--color-border-strong)] px-3 py-1.5 text-xs font-medium text-[var(--color-brand)] transition hover:bg-[var(--color-brand-soft)]"
              >
                协作工作台
              </Link>
            ) : null}
            <span className="hidden rounded-[4px] border border-[var(--color-border)] px-2.5 py-1 text-xs text-[var(--color-text-muted)] sm:inline-flex">
              覆盖信息由本次检索返回
            </span>
          </div>
        </header>

        <div className="mx-auto w-full max-w-[760px]">
          <SearchComposer onSubmit={handleSearchSubmit} />
        </div>

        <footer className="mx-auto flex w-full max-w-[760px] flex-col gap-2 border-t border-[var(--color-border)] pt-4 text-xs leading-5 text-[var(--color-text-muted)] sm:flex-row sm:items-center sm:justify-between">
          <span>数据覆盖和排序说明以结果页返回为准。</span>
          <span>原始案情不上送服务器；草稿与历史如启用仅存于本浏览器、可清除。</span>
        </footer>
      </div>
    </main>
  );
}
