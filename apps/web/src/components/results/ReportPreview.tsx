import { useState } from "react";

import type {
  ReportCaseEntry,
  ReportSection,
  ReportTemplate,
} from "../../lib/reportTemplate";

// M4-6 轻量报告模板预览（F18，仅轻量模板部分）。
// 组件只渲染父组件经 reportTemplate 纯函数组装好的报告骨架：模板结构 + 元数据 +
// 来源锚点 + 用户自填备注 + 系统生成的结构化占位与免责说明。组件本身不组装内容、
// 不生成法律意见、不下结论、不访问 storage、不向后端发送任何正文。
// 生成失败（缺清单 / 空清单）时父组件下发 failed 报告，这里给安全降级提示。
// 报告导出复用 M4-5 下载能力，由父组件完成。

type ReportPreviewProps = {
  report: ReportTemplate | null;
  // onGenerate: 触发（重新）组装报告。可选携带检索背景备注（用户自填短字段）。
  onGenerate: (backgroundNote: string) => void;
  // onDownload: 导出当前报告为 Markdown 文件；返回状态供展示成功 / 降级提示。
  onDownload: () => { ok: boolean; status: string };
  // onClose: 收起预览。
  onClose: () => void;
};

function anchorRef(anchor: { case_id: string; source_chunk_id: string }): string {
  return `${anchor.case_id}#${anchor.source_chunk_id}`;
}

export function ReportPreview({ report, onGenerate, onDownload, onClose }: ReportPreviewProps) {
  const [backgroundNote, setBackgroundNote] = useState("");
  const [feedback, setFeedback] = useState<{ ok: boolean; message: string } | null>(null);

  function runDownload() {
    let result: { ok: boolean; status: string };
    try {
      result = onDownload();
    } catch {
      setFeedback({ ok: false, message: "报告导出未完成，请稍后重试（不影响检索与清单）。" });
      return;
    }
    if (result.ok) {
      setFeedback({ ok: true, message: "已生成报告文件（仅含模板结构、元数据、来源引用与你的备注）。" });
    } else if (result.status === "degraded") {
      setFeedback({
        ok: false,
        message: "当前环境无法自动下载，已安全跳过；报告可在上方预览或复制，不影响检索与清单。",
      });
    } else {
      setFeedback({ ok: false, message: "报告导出未完成，请稍后重试（不影响检索与清单）。" });
    }
  }

  const degraded = report
    ? report.report_status === "degraded" || report.report_status === "failed"
    : false;

  return (
    <section
      aria-label="类案报告模板预览"
      className="mt-3 rounded-[6px] border border-dashed border-[var(--color-border-strong)] bg-[var(--color-surface-muted)] p-3"
    >
      <div className="flex items-start justify-between gap-2">
        <div>
          <h4 className="text-xs font-semibold text-[var(--color-text)]">类案报告模板（骨架）</h4>
          <p className="mt-0.5 text-[10px] leading-4 text-[var(--color-text-subtle)]">
            报告只整理模板结构、案号等元数据、来源引用与你的备注，不含裁判正文，不起草法律文书，不判断胜负或给出确定性法律结论，须人工复核。
          </p>
        </div>
        <button
          type="button"
          aria-label="收起报告预览"
          className="shrink-0 rounded-[6px] border border-[var(--color-border-strong)] px-2 py-0.5 text-[11px] text-[var(--color-text-muted)] hover:bg-[var(--color-surface)] focus:outline-none focus:ring-2 focus:ring-[var(--color-brand-soft)]"
          onClick={onClose}
        >
          收起
        </button>
      </div>

      <label className="mt-2 block text-[11px] text-[var(--color-text-muted)]">
        检索背景（选填，短备注；系统不自动生成案情或结论）
        <textarea
          value={backgroundNote}
          maxLength={240}
          rows={2}
          aria-label="检索背景备注"
          className="mt-1 block w-full resize-none rounded-[6px] border border-[var(--color-border)] bg-[var(--color-surface)] px-2 py-1 text-xs text-[var(--color-text)] outline-none focus:border-[var(--color-brand)] focus:ring-2 focus:ring-[var(--color-brand-soft)]"
          onChange={(event) => setBackgroundNote(event.target.value)}
        />
      </label>

      <div className="mt-2 flex flex-wrap items-center gap-2">
        <button
          type="button"
          className="rounded-[6px] bg-[var(--color-brand)] px-2.5 py-1 text-[11px] font-medium text-white hover:bg-[var(--color-brand-hover)] focus:outline-none focus:ring-2 focus:ring-[var(--color-brand-soft)]"
          onClick={() => {
            setFeedback(null);
            onGenerate(backgroundNote);
          }}
        >
          {report ? "重新生成报告" : "生成报告"}
        </button>
        {report && report.report_status !== "failed" ? (
          <button
            type="button"
            aria-label="导出报告为 Markdown"
            className="rounded-[6px] border border-[var(--color-border-strong)] px-2 py-1 text-[11px] text-[var(--color-text)] hover:bg-[var(--color-surface)] focus:outline-none focus:ring-2 focus:ring-[var(--color-brand-soft)]"
            onClick={runDownload}
          >
            导出 Markdown
          </button>
        ) : null}
      </div>

      {report ? (
        <ReportBody report={report} degraded={degraded} />
      ) : (
        <p className="mt-2 text-[11px] text-[var(--color-text-subtle)]">
          点击「生成报告」基于本清单整理一份轻量报告模板。
        </p>
      )}

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
    </section>
  );
}

// 报告正文渲染：按章节展示。降级态（仅清单概览 + 免责 / 失败）给明确提示。
function ReportBody({ report, degraded }: { report: ReportTemplate; degraded: boolean }) {
  return (
    <div className="mt-2 space-y-2 rounded-[6px] border border-[var(--color-border)] bg-[var(--color-surface)] p-2.5">
      {degraded ? (
        <p
          role="status"
          className="rounded-[4px] bg-[var(--color-surface-muted)] px-2 py-1 text-[11px] text-[var(--color-text-muted)]"
        >
          {report.report_status === "failed"
            ? "暂无法生成完整报告（清单为空或不可用），仅展示免责说明。请先向清单加入案例。"
            : "报告已降级为仅清单概览，可用信息有限，不影响检索与清单。"}
        </p>
      ) : null}
      {report.sections.map((section, index) => (
        <ReportSectionView key={`${section.kind}-${index}`} section={section} />
      ))}
    </div>
  );
}

function ReportSectionView({ section }: { section: ReportSection }) {
  switch (section.kind) {
    case "search_background":
      return (
        <div>
          <h5 className="text-[11px] font-semibold text-[var(--color-text)]">{section.title}</h5>
          <p className="mt-0.5 whitespace-pre-wrap break-words text-[11px] text-[var(--color-text-muted)]">
            {section.user_note || section.placeholder}
          </p>
        </div>
      );
    case "list_overview":
      return (
        <div>
          <h5 className="text-[11px] font-semibold text-[var(--color-text)]">{section.title}</h5>
          <p className="mt-0.5 text-[11px] text-[var(--color-text-muted)]">
            清单名称：{section.list_title}　|　案例数量：{section.item_count}
          </p>
        </div>
      );
    case "case_entries":
      return (
        <div>
          <h5 className="text-[11px] font-semibold text-[var(--color-text)]">{section.title}</h5>
          {section.entries.length === 0 ? (
            <p className="mt-0.5 text-[11px] text-[var(--color-text-subtle)]">（清单暂无案例）</p>
          ) : (
            <ol className="mt-1 space-y-1.5">
              {section.entries.map((entry) => (
                <ReportEntryView key={entry.case_id} entry={entry} />
              ))}
            </ol>
          )}
        </div>
      );
    case "review_points":
      return (
        <div>
          <h5 className="text-[11px] font-semibold text-[var(--color-text)]">{section.title}</h5>
          <ul className="mt-0.5 list-disc space-y-0.5 pl-4 text-[11px] text-[var(--color-text-muted)]">
            {section.points.map((point, index) => (
              <li key={index}>{point}</li>
            ))}
          </ul>
        </div>
      );
    case "disclaimer":
      return (
        <div>
          <h5 className="text-[11px] font-semibold text-[var(--color-text)]">{section.title}</h5>
          <div className="mt-0.5 space-y-0.5">
            {section.lines.map((line, index) => (
              <p key={index} className="text-[10px] leading-4 text-[var(--color-text-subtle)]">
                {line}
              </p>
            ))}
          </div>
        </div>
      );
    default:
      return null;
  }
}

function ReportEntryView({ entry }: { entry: ReportCaseEntry }) {
  const title = entry.case_number || "案号暂缺";
  const metaLine = [entry.court, entry.trial_level, entry.case_cause, entry.judgment_date]
    .filter(Boolean)
    .join(" · ");
  const hasAnchor = entry.source_anchors.length > 0;
  return (
    <li className="rounded-[4px] border border-[var(--color-border)] bg-[var(--color-bg)] px-2 py-1.5">
      <p className="text-[11px] font-medium text-[var(--color-text)]">
        {entry.ordinal}. {title}
      </p>
      {metaLine ? (
        <p className="mt-0.5 truncate text-[10px] text-[var(--color-text-muted)]">{metaLine}</p>
      ) : null}
      {entry.tag ? (
        <span className="mt-0.5 inline-flex rounded-[4px] bg-[var(--color-surface-muted)] px-1.5 py-0.5 text-[10px] text-[var(--color-text-muted)]">
          {entry.tag}
        </span>
      ) : null}
      {entry.note ? (
        <p className="mt-0.5 break-words text-[10px] text-[var(--color-text)]">备注：{entry.note}</p>
      ) : null}
      {hasAnchor ? (
        <p className="mt-0.5 truncate font-mono text-[10px] text-[var(--color-text-subtle)]">
          来源 {entry.source_anchors.map(anchorRef).join(" ; ")}
        </p>
      ) : (
        <p className="mt-0.5 text-[10px] text-[var(--color-text-subtle)]">来源引用暂缺</p>
      )}
      {entry.anchored_fragments.map((fragment, index) => (
        <p key={index} className="mt-0.5 break-words text-[10px] text-[var(--color-text-muted)]">
          摘录（待核）：{fragment.text}
          <span className="font-mono text-[var(--color-text-subtle)]">
            　[来源：{anchorRef(fragment.source_anchor)}]
          </span>
        </p>
      ))}
    </li>
  );
}
