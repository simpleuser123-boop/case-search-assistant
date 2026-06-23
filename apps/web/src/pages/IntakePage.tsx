import { useState } from "react";
import { Link } from "react-router-dom";

import { isIntakeEnabled } from "../config/featureFlags";
import {
  buildSearchProfileFromRaw,
  redactPII,
  type SearchProfileDraft,
} from "../intake/sanitize";
import {
  submitIntakeSearch,
  type IntakeApiResult,
  type IntakeCandidateRefView,
} from "../services/intakeApi";

// 录入端三阶段：本地输入 -> 脱敏预览（用户确认） -> 结果。
// 原始案情只存于本组件 React state（内存），绝不写入任何浏览器存储或网络请求体。
type IntakeStage = "input" | "preview" | "results";

type IntakeStatus =
  | { kind: "idle" }
  | { kind: "submitting" }
  | { kind: "error"; message: string }
  | { kind: "done" };

// 把已脱敏 profile 的结构化要素叠加用户「结构化辅助输入」（仍逐项再脱敏）。
function applyStructuredAssist(
  base: SearchProfileDraft,
  assist: { caseCause: string; region: string; trialLevel: string; keywords: string },
): SearchProfileDraft {
  const next: SearchProfileDraft = {
    case_cause: base.case_cause,
    region: base.region,
    trial_level_preference: base.trial_level_preference,
    dispute_focus_keywords: [...base.dispute_focus_keywords],
    query_text: base.query_text,
  };
  const caseCause = assist.caseCause.trim();
  if (caseCause) next.case_cause = redactPII(caseCause);
  const region = assist.region.trim();
  if (region) next.region = redactPII(region);
  const trialLevel = assist.trialLevel.trim();
  if (trialLevel) next.trial_level_preference = redactPII(trialLevel);
  const kws = assist.keywords
    .split(/[,，、\s]+/)
    .map((k) => redactPII(k.trim()))
    .filter(Boolean)
    .slice(0, 8);
  if (kws.length > 0) next.dispute_focus_keywords = kws;
  return next;
}

const DISABLED_REASON_TEXT: Record<string, string> = {
  INTAKE_DISABLED: "录入端当前未启用（后端 ENABLE_INTAKE=false）。",
};

export function IntakePage() {
  // flag off：DOM 不渲染任何录入端入口/页面（安全末态）。
  if (!isIntakeEnabled()) {
    return null;
  }
  return <IntakeWorkspace />;
}

function IntakeWorkspace() {
  // 原始口语化案情：仅内存 state，绝不上送 / 不入存储。
  const [rawCase, setRawCase] = useState("");
  const [assistCaseCause, setAssistCaseCause] = useState("");
  const [assistRegion, setAssistRegion] = useState("");
  const [assistTrialLevel, setAssistTrialLevel] = useState("");
  const [assistKeywords, setAssistKeywords] = useState("");

  const [stage, setStage] = useState<IntakeStage>("input");
  const [profile, setProfile] = useState<SearchProfileDraft | null>(null);
  const [status, setStatus] = useState<IntakeStatus>({ kind: "idle" });
  const [candidateRefs, setCandidateRefs] = useState<IntakeCandidateRefView[]>([]);
  const [degradedReasons, setDegradedReasons] = useState<string[]>([]);

  const canPreview = rawCase.trim().length > 0;

  // 本地脱敏预览：纯前端 sanitize.ts，把原始案情转 SearchProfile（白名单五字段）。
  function handlePreview() {
    const base = buildSearchProfileFromRaw(rawCase);
    const merged = applyStructuredAssist(base, {
      caseCause: assistCaseCause,
      region: assistRegion,
      trialLevel: assistTrialLevel,
      keywords: assistKeywords,
    });
    setProfile(merged);
    setStage("preview");
    setStatus({ kind: "idle" });
  }

  function handleBackToInput() {
    setStage("input");
    setStatus({ kind: "idle" });
  }

  // 用户显式确认后：仅 POST SearchProfile 白名单五字段；原始案情绝不进入请求体。
  async function handleConfirmAndSend() {
    if (!profile) {
      return;
    }
    setStatus({ kind: "submitting" });
    const result: IntakeApiResult = await submitIntakeSearch(profile, {
      mode: "standard",
      limit: 10,
    });
    if (result.ok) {
      setCandidateRefs(result.data.candidate_refs ?? []);
      setDegradedReasons(result.data.degraded_reasons ?? []);
      setStage("results");
      setStatus({ kind: "done" });
      return;
    }
    setStatus({ kind: "error", message: describeError(result) });
  }

  function handleReset() {
    setRawCase("");
    setAssistCaseCause("");
    setAssistRegion("");
    setAssistTrialLevel("");
    setAssistKeywords("");
    setProfile(null);
    setCandidateRefs([]);
    setDegradedReasons([]);
    setStage("input");
    setStatus({ kind: "idle" });
  }

  return (
    <main
      aria-label="案情录入端"
      className="min-h-[100dvh] bg-[var(--color-bg)] px-4 py-6 text-[var(--color-text)] sm:px-6 sm:py-10"
    >
      <div className="mx-auto flex w-full max-w-[1180px] flex-col gap-8">
        <header className="flex items-start justify-between border-b border-[var(--color-border)] pb-4">
          <div>
            <p className="text-base font-semibold text-[var(--color-text)]">
              案情录入端
            </p>
            <p className="mt-1 text-xs text-[var(--color-text-muted)]">
              本地脱敏后再检索：原始案情只留在本浏览器，不上送服务器
            </p>
          </div>
          <Link
            to="/"
            className="hidden rounded-[8px] border border-[var(--color-border-strong)] px-3 py-1.5 text-xs font-medium text-[var(--color-brand)] transition hover:bg-[var(--color-brand-soft)] sm:inline-flex"
          >
            返回检索助手
          </Link>
        </header>

        <ol className="flex flex-wrap gap-2 text-xs text-[var(--color-text-muted)]">
          <StepBadge active={stage === "input"} done={stage !== "input"} index={1} label="本地输入" />
          <StepBadge active={stage === "preview"} done={stage === "results"} index={2} label="脱敏预览·确认" />
          <StepBadge active={stage === "results"} done={false} index={3} label="类案结果" />
        </ol>

        {stage === "input" ? (
          <InputStage
            rawCase={rawCase}
            onRawCaseChange={setRawCase}
            assistCaseCause={assistCaseCause}
            onAssistCaseCauseChange={setAssistCaseCause}
            assistRegion={assistRegion}
            onAssistRegionChange={setAssistRegion}
            assistTrialLevel={assistTrialLevel}
            onAssistTrialLevelChange={setAssistTrialLevel}
            assistKeywords={assistKeywords}
            onAssistKeywordsChange={setAssistKeywords}
            canPreview={canPreview}
            onPreview={handlePreview}
          />
        ) : null}

        {stage === "preview" && profile ? (
          <PreviewStage
            profile={profile}
            status={status}
            onBack={handleBackToInput}
            onConfirm={handleConfirmAndSend}
          />
        ) : null}

        {stage === "results" ? (
          <ResultsStage
            candidateRefs={candidateRefs}
            degradedReasons={degradedReasons}
            onReset={handleReset}
            onBack={() => setStage("preview")}
          />
        ) : null}

        <footer className="border-t border-[var(--color-border)] pt-4 text-xs leading-5 text-[var(--color-text-muted)]">
          原始案情与当事人信息只保存在本浏览器内存、不写入本地存储、不随任何请求上送；仅脱敏后的检索要素离开浏览器。
        </footer>
      </div>
    </main>
  );
}

function describeError(result: Extract<IntakeApiResult, { ok: false }>): string {
  if (result.reason === "disabled") {
    return (
      (result.reasonCode && DISABLED_REASON_TEXT[result.reasonCode]) ||
      "录入端当前未启用，请稍后再试或联系管理员。"
    );
  }
  if (result.reason === "timeout") {
    return "检索请求超时，请稍后重试。";
  }
  if (result.reason === "rejected") {
    return result.message || "提交内容未通过校验，请调整后重试。";
  }
  return "检索服务暂时不可用，请稍后重试。";
}

function StepBadge({
  active,
  done,
  index,
  label,
}: {
  active: boolean;
  done: boolean;
  index: number;
  label: string;
}) {
  return (
    <li
      className={[
        "inline-flex items-center gap-1.5 rounded-[6px] border px-2.5 py-1",
        active
          ? "border-[var(--color-brand)] bg-[var(--color-brand-soft)] text-[var(--color-brand)]"
          : done
            ? "border-[var(--color-border)] bg-[var(--color-surface-muted)] text-[var(--color-text-muted)]"
            : "border-[var(--color-border)] text-[var(--color-text-subtle)]",
      ].join(" ")}
    >
      <span className="font-mono text-[11px]">{index}</span>
      <span>{label}</span>
    </li>
  );
}

function InputStage(props: {
  rawCase: string;
  onRawCaseChange: (value: string) => void;
  assistCaseCause: string;
  onAssistCaseCauseChange: (value: string) => void;
  assistRegion: string;
  onAssistRegionChange: (value: string) => void;
  assistTrialLevel: string;
  onAssistTrialLevelChange: (value: string) => void;
  assistKeywords: string;
  onAssistKeywordsChange: (value: string) => void;
  canPreview: boolean;
  onPreview: () => void;
}) {
  return (
    <section className="flex flex-col gap-5 rounded-[10px] border border-[var(--color-border)] bg-[var(--color-surface)] p-5 sm:p-6">
      <div>
        <label
          htmlFor="intake-raw-case"
          className="text-sm font-medium text-[var(--color-text)]"
        >
          原始案情（口语化描述）
        </label>
        <p className="mt-1 text-xs text-[var(--color-text-muted)]">
          可直接粘贴含当事人称呼的口语化案情。下一步会在本浏览器本地脱敏，只有脱敏后的检索要素会被发送。
        </p>
        <textarea
          id="intake-raw-case"
          aria-label="原始案情"
          value={props.rawCase}
          onChange={(event) => props.onRawCaseChange(event.target.value)}
          rows={8}
          placeholder="例如：原告主张被告拖欠货款，双方就买卖合同的付款义务存在争议……"
          className="mt-2 w-full resize-y rounded-[8px] border border-[var(--color-border-strong)] bg-[var(--color-surface)] px-3 py-2 text-sm leading-6 text-[var(--color-text)] outline-none focus:ring-2 focus:ring-[var(--color-brand-soft)]"
        />
      </div>

      <fieldset className="grid gap-4 border-t border-[var(--color-border)] pt-4 sm:grid-cols-2">
        <legend className="text-xs font-medium text-[var(--color-text-muted)]">
          结构化要素（可选，留空将由本地自动抽取）
        </legend>
        <AssistField
          id="intake-case-cause"
          label="案由"
          value={props.assistCaseCause}
          onChange={props.onAssistCaseCauseChange}
          placeholder="如：买卖合同纠纷"
        />
        <AssistField
          id="intake-region"
          label="地域"
          value={props.assistRegion}
          onChange={props.onAssistRegionChange}
          placeholder="如：北京"
        />
        <AssistField
          id="intake-trial-level"
          label="审级倾向"
          value={props.assistTrialLevel}
          onChange={props.onAssistTrialLevelChange}
          placeholder="如：二审"
        />
        <AssistField
          id="intake-keywords"
          label="争议焦点关键词"
          value={props.assistKeywords}
          onChange={props.onAssistKeywordsChange}
          placeholder="用逗号分隔，如：货款, 违约责任"
        />
      </fieldset>

      <div className="flex items-center justify-end gap-3">
        <button
          type="button"
          disabled={!props.canPreview}
          onClick={props.onPreview}
          className="inline-flex h-10 items-center justify-center rounded-[8px] bg-[var(--color-brand)] px-5 text-sm font-medium text-white transition hover:bg-[var(--color-brand-hover)] focus:outline-none focus:ring-2 focus:ring-[var(--color-brand-soft)] disabled:cursor-not-allowed disabled:opacity-50"
        >
          本地脱敏预览
        </button>
      </div>
    </section>
  );
}

function AssistField({
  id,
  label,
  value,
  onChange,
  placeholder,
}: {
  id: string;
  label: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
}) {
  return (
    <div>
      <label htmlFor={id} className="text-xs font-medium text-[var(--color-text)]">
        {label}
      </label>
      <input
        id={id}
        aria-label={label}
        type="text"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder}
        className="mt-1 w-full rounded-[8px] border border-[var(--color-border-strong)] bg-[var(--color-surface)] px-3 py-2 text-sm text-[var(--color-text)] outline-none focus:ring-2 focus:ring-[var(--color-brand-soft)]"
      />
    </div>
  );
}

function PreviewStage({
  profile,
  status,
  onBack,
  onConfirm,
}: {
  profile: SearchProfileDraft;
  status: IntakeStatus;
  onBack: () => void;
  onConfirm: () => void;
}) {
  const submitting = status.kind === "submitting";
  const rows: Array<{ label: string; value: string }> = [
    { label: "案由 case_cause", value: profile.case_cause || "（未抽取）" },
    { label: "地域 region", value: profile.region || "（未抽取）" },
    {
      label: "审级倾向 trial_level_preference",
      value: profile.trial_level_preference || "（未抽取）",
    },
    {
      label: "争议焦点 dispute_focus_keywords",
      value:
        profile.dispute_focus_keywords.length > 0
          ? profile.dispute_focus_keywords.join("、")
          : "（未抽取）",
    },
    { label: "脱敏查询 query_text", value: profile.query_text || "（空）" },
  ];

  return (
    <section
      aria-label="脱敏预览"
      className="flex flex-col gap-5 rounded-[10px] border border-[var(--color-border)] bg-[var(--color-surface)] p-5 sm:p-6"
    >
      <div>
        <p className="text-sm font-medium text-[var(--color-text)]">
          将要发送的脱敏后内容
        </p>
        <p className="mt-1 text-xs text-[var(--color-text-muted)]">
          以下为本地脱敏后、确认后将发送给检索服务的全部内容（白名单五字段）。原始案情不在其中、不会上送。
        </p>
      </div>

      <dl className="grid gap-3">
        {rows.map((row) => (
          <div
            key={row.label}
            className="grid gap-1 rounded-[8px] border border-[var(--color-border)] bg-[var(--color-surface-muted)] px-3 py-2 sm:grid-cols-[220px_1fr] sm:items-baseline"
          >
            <dt className="font-mono text-[11px] text-[var(--color-text-muted)]">
              {row.label}
            </dt>
            <dd className="break-words text-sm leading-6 text-[var(--color-text)]">
              {row.value}
            </dd>
          </div>
        ))}
      </dl>

      {status.kind === "error" ? (
        <p
          role="alert"
          className="rounded-[8px] border border-[var(--color-danger)] bg-[var(--color-danger-soft)] px-3 py-2 text-sm text-[var(--color-danger)]"
        >
          {status.message}
        </p>
      ) : null}

      <div className="flex flex-col gap-3 border-t border-[var(--color-border)] pt-4 sm:flex-row sm:items-center sm:justify-between">
        <button
          type="button"
          onClick={onBack}
          disabled={submitting}
          className="inline-flex h-10 items-center justify-center rounded-[8px] border border-[var(--color-border-strong)] px-4 text-sm font-medium text-[var(--color-text-muted)] transition hover:bg-[var(--color-surface-muted)] disabled:cursor-not-allowed disabled:opacity-50"
        >
          返回修改
        </button>
        <button
          type="button"
          onClick={onConfirm}
          disabled={submitting}
          className="inline-flex h-10 items-center justify-center rounded-[8px] bg-[var(--color-brand)] px-5 text-sm font-medium text-white transition hover:bg-[var(--color-brand-hover)] focus:outline-none focus:ring-2 focus:ring-[var(--color-brand-soft)] disabled:cursor-not-allowed disabled:opacity-50"
        >
          {submitting ? "检索中…" : "确认并仅发送脱敏内容"}
        </button>
      </div>
    </section>
  );
}

function ResultsStage({
  candidateRefs,
  degradedReasons,
  onReset,
  onBack,
}: {
  candidateRefs: IntakeCandidateRefView[];
  degradedReasons: string[];
  onReset: () => void;
  onBack: () => void;
}) {
  return (
    <section aria-label="类案候选结果" className="flex flex-col gap-4">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        <p className="text-sm font-medium text-[var(--color-text)]">
          类案候选（{candidateRefs.length}）
        </p>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={onBack}
            className="inline-flex h-9 items-center justify-center rounded-[8px] border border-[var(--color-border-strong)] px-3 text-xs font-medium text-[var(--color-text-muted)] transition hover:bg-[var(--color-surface-muted)]"
          >
            返回脱敏预览
          </button>
          <button
            type="button"
            onClick={onReset}
            className="inline-flex h-9 items-center justify-center rounded-[8px] border border-[var(--color-border-strong)] px-3 text-xs font-medium text-[var(--color-brand)] transition hover:bg-[var(--color-brand-soft)]"
          >
            重新录入
          </button>
        </div>
      </div>

      {degradedReasons.length > 0 ? (
        <p className="rounded-[8px] border border-[var(--color-warning)] bg-[var(--color-warning-soft)] px-3 py-2 text-xs text-[var(--color-warning)]">
          本次检索为降级结果：{degradedReasons.join("、")}
        </p>
      ) : null}

      {candidateRefs.length === 0 ? (
        <p className="rounded-[10px] border border-[var(--color-border)] bg-[var(--color-surface)] px-4 py-6 text-center text-sm text-[var(--color-text-muted)]">
          未命中可锚定来源的类案候选。可返回调整案情要素后重试。
        </p>
      ) : (
        <ul className="grid gap-3">
          {candidateRefs.map((ref, index) => (
            <CandidateRefCard key={`${ref.case_id}-${index}`} refItem={ref} index={index} />
          ))}
        </ul>
      )}
    </section>
  );
}

function CandidateRefCard({
  refItem,
  index,
}: {
  refItem: IntakeCandidateRefView;
  index: number;
}) {
  const meta = [
    refItem.case_number || "案号暂缺",
    refItem.court || "法院暂缺",
    refItem.trial_level || "审级暂缺",
    refItem.case_cause || "案由暂缺",
    refItem.judgment_date || "日期暂缺",
  ];
  const anchors = (refItem.source_anchors || []).slice(0, 4);

  return (
    <li className="rounded-[8px] border border-[var(--color-border)] bg-[var(--color-surface)] p-4 sm:p-5">
      <div className="flex items-start gap-2">
        <span className="mt-0.5 shrink-0 rounded-[4px] bg-[var(--color-brand-soft)] px-2 py-1 text-xs font-medium text-[var(--color-brand)]">
          #{index + 1}
        </span>
        <div className="min-w-0">
          <p className="break-words font-mono text-sm text-[var(--color-text)]">
            {refItem.case_id}
          </p>
          <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-xs leading-5 text-[var(--color-text-muted)]">
            {meta.map((item) => (
              <span key={item}>{item}</span>
            ))}
          </div>
        </div>
      </div>

      <div className="mt-3 flex flex-col gap-3 border-t border-[var(--color-border)] pt-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex min-w-0 flex-wrap gap-2">
          {anchors.length > 0 ? (
            anchors.map((anchor) => (
              <span
                key={`${anchor.case_id}-${anchor.source_chunk_id}`}
                className="max-w-full truncate rounded-[4px] bg-[var(--color-surface-muted)] px-2 py-1 font-mono text-[11px] text-[var(--color-text-muted)]"
                title={`case_id: ${anchor.case_id}; source_chunk_id: ${anchor.source_chunk_id}`}
              >
                来源 {anchor.source_chunk_id}
              </span>
            ))
          ) : (
            <span className="text-xs text-[var(--color-text-subtle)]">来源片段暂缺</span>
          )}
        </div>
        <Link
          to={`/?case=${encodeURIComponent(refItem.case_id)}`}
          className="inline-flex h-9 shrink-0 items-center justify-center rounded-[8px] border border-[var(--color-border-strong)] bg-[var(--color-surface)] px-3 text-sm font-medium text-[var(--color-brand)] transition hover:bg-[var(--color-brand-soft)] focus:outline-none focus:ring-2 focus:ring-[var(--color-brand-soft)]"
        >
          在检索助手中查看
        </Link>
      </div>
    </li>
  );
}
