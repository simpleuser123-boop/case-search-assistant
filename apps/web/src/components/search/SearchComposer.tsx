import { useMemo, useRef, useState } from "react";

import {
  type SearchTrigger,
  validateSearchInput,
} from "../../lib/searchValidation";
import { useAnalytics } from "../../hooks/useAnalytics";

const MAX_RECOMMENDED_LENGTH = 500;

const EXAMPLES = [
  "消费者购买电热水壶使用两周后漏电受伤，商家称系使用不当。现争议产品是否存在缺陷、经营者是否应承担赔偿责任。",
  "车辆低速变道时对方突然倒地并主张高额修车和误工损失，现场视频显示接触轻微。需要检索碰瓷、交通事故责任认定相关类案。",
  "买卖合同约定分批交付设备，买方已付款但卖方多次延期交货并拒绝退还预付款。需要检索合同履行、迟延交付和解除责任。",
];

export type SearchComposerSubmitMeta = {
  inputLength: number;
  trigger: SearchTrigger;
  hasDraftRestored: boolean;
};

type SearchComposerProps = {
  onSubmit: (
    query: string,
    meta: SearchComposerSubmitMeta
  ) => void | Promise<void>;
};

export function SearchComposer({ onSubmit }: SearchComposerProps) {
  const analytics = useAnalytics();
  const [value, setValue] = useState("");
  const [showValidation, setShowValidation] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

  const validation = useMemo(() => validateSearchInput(value), [value]);
  const inputLength = Array.from(value).length;
  const isOverRecommendedLength = inputLength > MAX_RECOMMENDED_LENGTH;
  const shouldShowValidation = showValidation || Boolean(value);
  const errorMessage =
    shouldShowValidation && !validation.isValid ? validation.message : "";
  const canSubmit = validation.isValid && !isSubmitting;

  const describedBy = [
    "case-query-hint",
    "case-query-count",
    errorMessage ? "case-query-error" : "",
    isOverRecommendedLength ? "case-query-warning" : "",
  ]
    .filter(Boolean)
    .join(" ");

  function fillExample(example: string) {
    setValue(example);
    setShowValidation(false);
    textareaRef.current?.focus();
  }

  async function submit(trigger: SearchTrigger) {
    const nextValidation = validateSearchInput(value);
    if (!nextValidation.isValid) {
      setShowValidation(true);
      return;
    }

    setIsSubmitting(true);
    void analytics.trackSearchSubmit({
      input_length: Array.from(nextValidation.cleaned).length,
      trigger,
      has_draft_restored: false,
    });
    try {
      await onSubmit(nextValidation.cleaned, {
        inputLength: Array.from(nextValidation.cleaned).length,
        trigger,
        hasDraftRestored: false,
      });
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <section
      aria-labelledby="search-composer-title"
      className="mx-auto w-full max-w-[760px]"
    >
      <div className="space-y-3">
        <div className="space-y-1.5">
          <p className="text-sm font-medium text-[var(--color-brand)]">
            类案检索
          </p>
          <h1
            id="search-composer-title"
            className="text-xl font-semibold leading-snug text-[var(--color-text)] sm:text-2xl md:text-3xl"
          >
            输入案情，检索可复核的相似案例
          </h1>
          <p className="max-w-2xl text-sm leading-6 text-[var(--color-text-muted)]">
            可直接粘贴事实经过、争议焦点或当事人主张。首页输入只保留在当前页面状态中。
          </p>
        </div>

        <form
          className="space-y-3"
          onSubmit={(event) => {
            event.preventDefault();
            void submit("button");
          }}
        >
          <div className="space-y-2">
            <div className="flex items-end justify-between gap-3">
              <label
                htmlFor="case-query"
                className="text-sm font-medium text-[var(--color-text)]"
              >
                案情描述
              </label>
            </div>

            <div className="relative">
              <textarea
                ref={textareaRef}
                id="case-query"
                value={value}
                disabled={isSubmitting}
                aria-describedby={describedBy}
                aria-invalid={Boolean(errorMessage)}
                className="min-h-[148px] max-h-[260px] w-full resize-y rounded-[8px] border border-[var(--color-border)] bg-[var(--color-surface)] px-4 py-3 pb-10 text-base leading-7 text-[var(--color-text)] outline-none transition focus:border-[var(--color-brand)] focus:ring-2 focus:ring-[var(--color-brand-soft)] disabled:bg-[var(--color-surface-muted)]"
                placeholder="例如：买卖合同约定分批交付设备，买方已付款但卖方多次延期交货，双方对解除合同和返还预付款发生争议。"
                onChange={(event) => {
                  setValue(event.target.value);
                  setShowValidation(false);
                }}
                onKeyDown={(event) => {
                  if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
                    event.preventDefault();
                    void submit("keyboard");
                  }
                }}
              />
              <span
                id="case-query-count"
                className={`absolute bottom-3 right-3 rounded bg-[var(--color-surface)] px-1.5 py-0.5 font-mono text-xs ${
                  isOverRecommendedLength
                    ? "text-[var(--color-warning)]"
                    : "text-[var(--color-text-muted)]"
                }`}
              >
                {inputLength}/500
              </span>
            </div>

            <div className="min-h-[44px] space-y-1">
              <p
                id="case-query-hint"
                className="text-xs leading-5 text-[var(--color-text-muted)]"
              >
                建议包含行为经过、损害结果、争议焦点，便于按事实相似度优先排序。
              </p>
              {errorMessage ? (
                <p
                  id="case-query-error"
                  role="alert"
                  className="text-xs leading-5 text-[var(--color-danger)]"
                >
                  {errorMessage}
                </p>
              ) : null}
              {isOverRecommendedLength ? (
                <p
                  id="case-query-warning"
                  className="text-xs leading-5 text-[var(--color-warning)]"
                >
                  已超过 500 字，仍可提交；建议保留关键事实和争议焦点。
                </p>
              ) : null}
            </div>
          </div>

          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <button
              type="submit"
              disabled={!canSubmit}
              className="inline-flex h-11 min-w-[128px] items-center justify-center rounded-[8px] bg-[var(--color-brand)] px-5 text-sm font-medium text-white transition hover:bg-[var(--color-brand-hover)] active:translate-y-px disabled:cursor-not-allowed disabled:bg-[var(--color-border-strong)] disabled:text-white"
            >
              {isSubmitting ? "检索中..." : "开始检索"}
            </button>
            <p className="text-xs leading-5 text-[var(--color-text-muted)]">
              前端事件仅记录输入长度和触发方式，不记录原始案情。
            </p>
          </div>
        </form>
      </div>

      <div className="mt-7 space-y-3">
        <h2 className="text-sm font-medium text-[var(--color-text)]">
          示例案情
        </h2>
        <div className="grid gap-2">
          {EXAMPLES.map((example) => (
            <button
              key={example}
              type="button"
              className="rounded-[8px] border border-[var(--color-border)] bg-[var(--color-surface)] px-4 py-3 text-left text-sm leading-6 text-[var(--color-text)] transition hover:border-[var(--color-border-strong)] hover:bg-[var(--color-surface-muted)] focus:outline-none focus:ring-2 focus:ring-[var(--color-brand-soft)]"
              onClick={() => fillExample(example)}
            >
              {example}
            </button>
          ))}
        </div>
      </div>
    </section>
  );
}
