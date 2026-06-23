// M4-6 轻量报告模板生成（F18，仅取轻量模板部分）。
//
// 隐私与内容边界（M4-1 合同 / 止损线）：
//   - 报告只由**模板结构 + 元数据 + 来源锚点 + 用户自填短字段 + 系统生成的结构化
//     占位**组成：案号 case_number、法院 court、审级 trial_level、案由 case_cause、
//     裁判日期 judgment_date、来源锚点 source_anchors（case_id + source_chunk_id）、
//     用户自填 note / tag、用户自填检索背景备注，以及系统生成的待复核要点与免责说明。
//   - 绝不写入裁判文书正文、摘要正文、要旨正文、chunk 正文、原始案情，也不写入原始
//     query 或任何自由长文本。
//   - **不自动起草**代理词 / 诉状 / 答辩状 / 庭审提纲，**不输出**胜诉 / 败诉概率或
//     任何确定性法律结论 / 确定性论证。
//   - AI 加工片段（若有）**必须携带来源锚点**（case_id + source_chunk_id），且必须
//     归属清单内案例、长度受限、不含禁用话术；无锚点 / 越界 / 含禁用话术的片段一律
//     丢弃，不进入报告。默认无 AI 片段，报告即纯元数据骨架。
//   - 报告只在浏览器本地组装与预览，不上送后端持久层；报告导出复用 M4-5 导出能力，
//     仅本地生成下载。报告行为绝不影响主结果排序 / 召回 / source selection。
//   - 生成失败安全降级为「仅清单概览 + 免责说明」，绝不抛出、绝不破坏主链路。
//   - 日志只记录 event / status / reason_code / section_count / item_count，绝不含
//     正文、案号、note、tag、title、query。
//
// 本模块为纯函数 + 可注入下载器，便于在无真实浏览器下单测。

import type { CaseListItem, CaseListRecord } from "./caseList";
import {
  containsForbiddenExportPhrase,
  getBrowserDownloader,
  type DownloaderLike,
  type ExportFormat,
} from "./caseListExport";

// 报告章节类型（对应步骤要求：检索背景占位 / 清单概览 / 逐案元数据+来源锚点 /
// 待人工复核要点 / 免责声明）。
export type ReportSectionKind =
  | "search_background"
  | "list_overview"
  | "case_entries"
  | "review_points"
  | "disclaimer";

export type ReportStatus = "generated" | "degraded" | "failed";

export type ReportReasonCode =
  | "list_not_found"
  | "empty_list"
  | "render_failed"
  | "assembly_failed"
  | "degraded_overview_only"
  | "download_unavailable"
  | "download_failed";

// 报告用来源锚点：只保留可追溯所需的最小标识字段，绝不含正文。
export type ReportAnchor = {
  case_id: string;
  source_chunk_id: string;
  anchor_type?: string;
  chunk_type?: string | null;
};

// AI 加工片段（可选输入）。必须携带来源锚点，否则不进入报告。
// text 仅为对来源的简短结构化提示，受长度与禁用话术校验，绝不是正文 / 原始案情。
export type ReportAnchoredFragment = {
  case_id: string;
  text: string;
  source_anchor: ReportAnchor;
};

// 单个逐案条目：全部为引用 / 元数据 / 锚点 / 用户自填短字段 + 已锚定 AI 片段，零正文。
export type ReportCaseEntry = {
  ordinal: number;
  case_id: string;
  case_number: string;
  court: string;
  trial_level: string;
  case_cause: string;
  judgment_date: string;
  source_anchors: ReportAnchor[];
  note: string;
  tag: string;
  // anchored_fragments: 仅含通过锚点 / 长度 / 话术校验的 AI 片段；默认空。
  anchored_fragments: ReportAnchoredFragment[];
};

// 报告章节（判别联合）。每个章节只承载结构化字段，无自由长正文。
export type ReportSection =
  | {
      kind: "search_background";
      title: string;
      placeholder: string;
      user_note: string;
    }
  | {
      kind: "list_overview";
      title: string;
      list_title: string;
      item_count: number;
      generated_at: string;
    }
  | { kind: "case_entries"; title: string; entries: ReportCaseEntry[] }
  | { kind: "review_points"; title: string; points: string[] }
  | { kind: "disclaimer"; title: string; lines: string[] };

// 一份轻量报告模板（对应合同 report_template）。只含引用 / 配置 / 结构化占位，无正文。
export type ReportTemplate = {
  report_id: string;
  list_id: string;
  report_title: string;
  generated_at: string;
  report_status: ReportStatus;
  degrade_reason: ReportReasonCode | null;
  item_count: number;
  sections: ReportSection[];
};

// 用户自填检索背景备注上限（短字段）。
export const REPORT_BACKGROUND_MAX_CHARS = 240;
// AI 加工片段文本上限（短提示，非正文）。
export const REPORT_FRAGMENT_MAX_CHARS = 160;

// 报告标题占位上限（短字段）。
export const REPORT_TITLE_MAX_CHARS = 60;

function nowIso(): string {
  return new Date().toISOString();
}

function clean(value: string | null | undefined): string {
  return (value || "").trim();
}

// 折叠空白并截断到上限，避免长正文混入报告结构。
function truncateShort(value: string | null | undefined, maxChars: number): string {
  const collapsed = (value || "").replace(/\s+/g, " ").trim();
  const chars = Array.from(collapsed);
  if (chars.length <= maxChars) {
    return collapsed;
  }
  return chars.slice(0, maxChars).join("");
}

// 生成报告 id：优先 crypto.randomUUID，回退时间戳 + 随机串。仅本地标识，无语义。
function generateReportId(): string {
  try {
    if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
      return `report_${crypto.randomUUID()}`;
    }
  } catch {
    // 忽略，走回退。
  }
  return `report_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`;
}

// ---------- 免责 / 待复核要点（强制写入报告）----------

// 报告强制免责说明。措辞严格规避绝对话术与诉讼结果判断：
//   - 不出现「已查全 / 保证无遗漏 / 查全率」等覆盖承诺；
//   - 不出现胜诉 / 败诉概率或确定性法律结论；
//   - 明确：内容为检索整理、需律师人工复核、不构成法律意见、不承诺已查全。
export const REPORT_DISCLAIMER_LINES: readonly string[] = [
  "本报告由「类案检索助手」基于类案清单自动整理，仅包含案例元数据、来源引用与用户自填备注，不含裁判文书正文或原始案情。",
  "本报告为检索与人工筛选的阶段性整理结果，可能存在未覆盖的案例，不代表对相关案件的完整检索，亦不对检索完整性作出承诺。",
  "本报告不提供胜诉或败诉等诉讼结果判断，不构成确定性法律结论或法律意见，不可直接作为代理词、诉状、答辩状等法律文书使用。",
  "报告内容须由律师结合权威数据库与原始裁判文书进行人工复核，并自行判断案例的相关性与适用性。",
];

// 系统生成的「待人工复核要点」——只给中性的复核动作清单，不含任何结论或胜负判断。
export const REPORT_REVIEW_POINTS: readonly string[] = [
  "逐案核对案号、法院、审级、案由与裁判日期是否与原始裁判文书一致。",
  "通过来源引用回到原文，确认每条引用与案件事实、争议焦点的对应关系。",
  "评估清单内案例与本案在关键事实与争议焦点上的相似度与差异点。",
  "确认是否存在清单未覆盖的相关案例，必要时在权威数据库补充检索。",
  "结合最新法律法规与司法解释，自行判断案例的可适用性与时效性。",
];

// 检索背景章节的中性占位（用户可在此基础上自行补充，不由系统下结论）。
export const REPORT_BACKGROUND_PLACEHOLDER =
  "（请在此补充检索背景：案件类型、争议焦点、检索目的与范围等。本栏为人工填写占位，系统不自动生成案情或结论。）";

// 禁用话术校验：复用 M4-5 导出的禁用词表，覆盖胜负概率 / 查全率等绝对话术。
export function reportContainsForbiddenPhrase(text: string): boolean {
  return containsForbiddenExportPhrase(text || "");
}

// ---------- 锚点与 AI 片段守门（无锚点 / 越界 / 含禁用话术一律丢弃）----------

// 把清单项锚点规整为报告锚点：只保留 case_id + source_chunk_id 齐全、且归属本案的锚点。
export function sanitizeReportAnchors(
  item: Pick<CaseListItem, "case_id" | "source_anchors">
): ReportAnchor[] {
  const caseId = clean(item.case_id);
  const result: ReportAnchor[] = [];
  const seen = new Set<string>();
  for (const anchor of item.source_anchors || []) {
    const aCaseId = clean(anchor?.case_id);
    const chunkId = clean(anchor?.source_chunk_id);
    if (!aCaseId || !chunkId || aCaseId !== caseId) {
      continue;
    }
    const key = `${chunkId}:${clean(anchor?.anchor_type)}`;
    if (seen.has(key)) {
      continue;
    }
    seen.add(key);
    result.push({
      case_id: aCaseId,
      source_chunk_id: chunkId,
      anchor_type: clean(anchor?.anchor_type) || "case_record",
      chunk_type:
        typeof anchor?.chunk_type === "string" && anchor.chunk_type.trim()
          ? anchor.chunk_type.trim()
          : null,
    });
  }
  return result;
}

// AI 加工片段守门：仅当片段满足以下全部条件才进入报告，否则丢弃——
//   1) 归属清单内某案例（case_id 在 allowedCaseIds 内）；
//   2) 携带完整来源锚点（case_id + source_chunk_id，且锚点 case_id 与片段 case_id 一致）；
//   3) 文本非空、截断到上限、不含禁用话术。
// 默认无片段输入即返回空——报告退化为纯元数据骨架。
export function filterAnchoredFragments(
  fragments: ReportAnchoredFragment[] | null | undefined,
  caseId: string,
  allowedCaseIds: Set<string>
): ReportAnchoredFragment[] {
  const cid = clean(caseId);
  if (!cid || !allowedCaseIds.has(cid)) {
    return [];
  }
  const result: ReportAnchoredFragment[] = [];
  for (const fragment of fragments || []) {
    if (!fragment || clean(fragment.case_id) !== cid) {
      continue;
    }
    const anchor = fragment.source_anchor;
    const anchorCaseId = clean(anchor?.case_id);
    const chunkId = clean(anchor?.source_chunk_id);
    // 无锚点或锚点不归属本案：丢弃。
    if (!anchorCaseId || !chunkId || anchorCaseId !== cid) {
      continue;
    }
    const text = truncateShort(fragment.text, REPORT_FRAGMENT_MAX_CHARS);
    // 空文本或含禁用话术：丢弃。
    if (!text || reportContainsForbiddenPhrase(text)) {
      continue;
    }
    result.push({
      case_id: cid,
      text,
      source_anchor: {
        case_id: anchorCaseId,
        source_chunk_id: chunkId,
        anchor_type: clean(anchor?.anchor_type) || "case_record",
        chunk_type:
          typeof anchor?.chunk_type === "string" && anchor.chunk_type.trim()
            ? anchor.chunk_type.trim()
            : null,
      },
    });
  }
  return result;
}

// 从清单项构造逐案条目（元数据 + 锚点 + 用户自填 + 已守门的 AI 片段）。绝不读正文。
export function buildCaseEntry(
  item: CaseListItem,
  ordinal: number,
  fragmentsByCase: Map<string, ReportAnchoredFragment[]> | null,
  allowedCaseIds: Set<string>
): ReportCaseEntry {
  const caseId = clean(item.case_id);
  const anchored = fragmentsByCase
    ? filterAnchoredFragments(fragmentsByCase.get(caseId), caseId, allowedCaseIds)
    : [];
  return {
    ordinal,
    case_id: caseId,
    case_number: clean(item.case_number),
    court: clean(item.court),
    trial_level: clean(item.trial_level),
    case_cause: clean(item.case_cause),
    judgment_date: clean(item.judgment_date),
    source_anchors: sanitizeReportAnchors(item),
    note: clean(item.note),
    tag: clean(item.tag),
    anchored_fragments: anchored,
  };
}

// ---------- 报告组装主入口 ----------

export type BuildReportOptions = {
  // reportTitle: 报告标题占位（用户自填短字段）；缺省回退清单标题。
  reportTitle?: string;
  // backgroundNote: 用户自填检索背景备注（短字段）；不由系统生成案情或结论。
  backgroundNote?: string;
  // anchoredFragments: 可选 AI 加工片段；每条必须携带来源锚点，否则被守门丢弃。
  anchoredFragments?: ReportAnchoredFragment[] | null;
};

// 仅清单概览 + 免责的降级报告（生成失败时的安全态）。不抛出。
function buildOverviewOnlyReport(
  list: CaseListRecord,
  reason: ReportReasonCode
): ReportTemplate {
  const generatedAt = nowIso();
  const listTitle = clean(list.list_title) || "未命名类案清单";
  return {
    report_id: generateReportId(),
    list_id: clean(list.list_id),
    report_title: listTitle,
    generated_at: generatedAt,
    report_status: "degraded",
    degrade_reason: reason,
    item_count: list.items.length,
    sections: [
      {
        kind: "list_overview",
        title: "清单概览",
        list_title: listTitle,
        item_count: list.items.length,
        generated_at: generatedAt,
      },
      { kind: "disclaimer", title: "免责说明", lines: [...REPORT_DISCLAIMER_LINES] },
    ],
  };
}

// 把一张清单组装为轻量报告模板。任何异常都安全降级为「仅清单概览 + 免责」，绝不抛出。
// 报告只含模板结构 + 元数据 + 来源锚点 + 用户自填 + 系统占位；AI 片段经守门，无锚点不进入。
export function buildReportTemplate(
  list: CaseListRecord | null | undefined,
  options: BuildReportOptions = {}
): ReportTemplate {
  // 缺清单：彻底失败态（无可降级内容）。
  if (!list || !clean(list.list_id)) {
    return {
      report_id: generateReportId(),
      list_id: clean(list?.list_id),
      report_title: "",
      generated_at: nowIso(),
      report_status: "failed",
      degrade_reason: "list_not_found",
      item_count: 0,
      sections: [
        { kind: "disclaimer", title: "免责说明", lines: [...REPORT_DISCLAIMER_LINES] },
      ],
    };
  }
  // 空清单：失败态（无案例可整理），但仍带免责。
  if (!list.items || list.items.length === 0) {
    return {
      report_id: generateReportId(),
      list_id: clean(list.list_id),
      report_title: clean(list.list_title) || "未命名类案清单",
      generated_at: nowIso(),
      report_status: "failed",
      degrade_reason: "empty_list",
      item_count: 0,
      sections: [
        { kind: "disclaimer", title: "免责说明", lines: [...REPORT_DISCLAIMER_LINES] },
      ],
    };
  }

  try {
    const generatedAt = nowIso();
    const listTitle = clean(list.list_title) || "未命名类案清单";
    const reportTitle = truncateShort(options.reportTitle, REPORT_TITLE_MAX_CHARS) || listTitle;
    const backgroundNote = truncateShort(options.backgroundNote, REPORT_BACKGROUND_MAX_CHARS);

    // 允许携带 AI 片段的案例集合（清单内全部 case_id）。
    const allowedCaseIds = new Set(
      list.items.map((item) => clean(item.case_id)).filter(Boolean)
    );
    const fragmentsByCase = new Map<string, ReportAnchoredFragment[]>();
    for (const fragment of options.anchoredFragments || []) {
      const cid = clean(fragment?.case_id);
      if (!cid) {
        continue;
      }
      const bucket = fragmentsByCase.get(cid) || [];
      bucket.push(fragment);
      fragmentsByCase.set(cid, bucket);
    }

    const entries = list.items.map((item, index) =>
      buildCaseEntry(item, index + 1, fragmentsByCase, allowedCaseIds)
    );

    const sections: ReportSection[] = [
      {
        kind: "search_background",
        title: "检索背景",
        placeholder: REPORT_BACKGROUND_PLACEHOLDER,
        user_note: backgroundNote,
      },
      {
        kind: "list_overview",
        title: "清单概览",
        list_title: listTitle,
        item_count: list.items.length,
        generated_at: generatedAt,
      },
      { kind: "case_entries", title: "逐案要点（元数据与来源锚点）", entries },
      { kind: "review_points", title: "待人工复核要点", points: [...REPORT_REVIEW_POINTS] },
      { kind: "disclaimer", title: "免责说明", lines: [...REPORT_DISCLAIMER_LINES] },
    ];

    return {
      report_id: generateReportId(),
      list_id: clean(list.list_id),
      report_title: reportTitle,
      generated_at: generatedAt,
      report_status: "generated",
      degrade_reason: null,
      item_count: list.items.length,
      sections,
    };
  } catch {
    // 组装异常：降级为仅清单概览 + 免责，不破坏主链路。
    return buildOverviewOnlyReport(list, "assembly_failed");
  }
}

// ---------- Markdown 渲染（预览 / 导出共用）----------

function escapeInline(value: string): string {
  return (value || "").replace(/\r?\n/g, " ").trim();
}

function anchorRef(anchor: ReportAnchor): string {
  return `${clean(anchor.case_id)}#${clean(anchor.source_chunk_id)}`;
}

function renderCaseEntry(entry: ReportCaseEntry): string[] {
  const lines: string[] = [];
  const heading = entry.case_number ? entry.case_number : "案号暂缺";
  lines.push(`### ${entry.ordinal}. ${escapeInline(heading)}`);
  const meta = [
    entry.court ? `法院：${escapeInline(entry.court)}` : "",
    entry.trial_level ? `审级：${escapeInline(entry.trial_level)}` : "",
    entry.case_cause ? `案由：${escapeInline(entry.case_cause)}` : "",
    entry.judgment_date ? `裁判日期：${escapeInline(entry.judgment_date)}` : "",
  ].filter(Boolean);
  if (meta.length > 0) {
    lines.push(meta.join("　|　"));
  }
  if (entry.tag) {
    lines.push(`标签：${escapeInline(entry.tag)}`);
  }
  if (entry.note) {
    lines.push(`备注：${escapeInline(entry.note)}`);
  }
  if (entry.source_anchors.length > 0) {
    lines.push(`来源引用：${entry.source_anchors.map(anchorRef).join(" ; ")}`);
  } else {
    lines.push("来源引用：暂缺（请回到原文补充核对）");
  }
  // AI 片段：每条都带来源锚点（守门已保证），无锚点内容不会出现在此。
  for (const fragment of entry.anchored_fragments) {
    lines.push(
      `> 摘录（待核）：${escapeInline(fragment.text)} [来源：${anchorRef(fragment.source_anchor)}]`
    );
  }
  lines.push("");
  return lines;
}

// 把报告模板渲染为 Markdown 文本（用于预览与导出）。仅结构化字段，绝无正文。
export function renderReportMarkdown(report: ReportTemplate): string {
  const lines: string[] = [];
  lines.push(`# ${escapeInline(report.report_title) || "类案检索报告"}`);
  lines.push("");
  for (const section of report.sections) {
    switch (section.kind) {
      case "search_background":
        lines.push(`## ${section.title}`);
        lines.push("");
        if (section.user_note) {
          lines.push(escapeInline(section.user_note));
        } else {
          lines.push(section.placeholder);
        }
        lines.push("");
        break;
      case "list_overview":
        lines.push(`## ${section.title}`);
        lines.push("");
        lines.push(`清单名称：${escapeInline(section.list_title)}`);
        lines.push(`案例数量：${section.item_count}`);
        lines.push(`生成时间：${escapeInline(section.generated_at)}`);
        lines.push("");
        break;
      case "case_entries":
        lines.push(`## ${section.title}`);
        lines.push("");
        if (section.entries.length === 0) {
          lines.push("（清单暂无案例）");
          lines.push("");
        } else {
          for (const entry of section.entries) {
            lines.push(...renderCaseEntry(entry));
          }
        }
        break;
      case "review_points":
        lines.push(`## ${section.title}`);
        lines.push("");
        for (const point of section.points) {
          lines.push(`- ${escapeInline(point)}`);
        }
        lines.push("");
        break;
      case "disclaimer":
        lines.push(`## ${section.title}`);
        lines.push("");
        for (const line of section.lines) {
          lines.push(`> ${line}`);
        }
        lines.push("");
        break;
      default:
        break;
    }
  }
  return lines.join("\n").trimEnd() + "\n";
}

// 运行时自检：渲染后的报告全文不得含禁用绝对话术 / 诉讼结果判断。供测试与导出前自检。
export function reportRenderHasForbiddenPhrase(report: ReportTemplate): boolean {
  return reportContainsForbiddenPhrase(renderReportMarkdown(report));
}

// ---------- 报告导出（复用 M4-5 下载能力，仅本地生成文件）----------

function reportFilename(report: ReportTemplate): string {
  const rawTitle = clean(report.report_title) || "类案检索报告";
  const safe = rawTitle.replace(/[\\/:*?"<>|]+/g, "_").replace(/\s+/g, "_").slice(0, 40);
  const stamp = nowIso().replace(/[:.]/g, "-");
  return `${safe || "case-report"}-${stamp}.md`;
}

export type DownloadReportResult = {
  report: ReportTemplate;
  content: string | null;
  filename: string | null;
};

// 把报告导出为 Markdown 文件并触发下载。复用 M4-5 注入式下载器抽象。任何异常都安全
// 降级（返回内容供预览 / 复制，状态降级），绝不抛出、绝不破坏主链路。
export function downloadReport(
  report: ReportTemplate,
  options: { downloader?: DownloaderLike | null; format?: ExportFormat } = {}
): DownloadReportResult {
  // 失败态报告（缺清单 / 空清单）不生成下载文件。
  if (report.report_status === "failed") {
    return { report, content: null, filename: null };
  }
  let content: string;
  try {
    content = renderReportMarkdown(report);
  } catch {
    return {
      report: { ...report, report_status: "degraded", degrade_reason: "render_failed" },
      content: null,
      filename: null,
    };
  }
  const filename = reportFilename(report);
  const downloader =
    options.downloader === undefined ? getBrowserDownloader() : options.downloader;
  if (!downloader) {
    return {
      report: { ...report, report_status: "degraded", degrade_reason: "download_unavailable" },
      content,
      filename,
    };
  }
  try {
    downloader({ filename, content, mimeType: "text/markdown;charset=utf-8" });
  } catch {
    return {
      report: { ...report, report_status: "degraded", degrade_reason: "download_failed" },
      content,
      filename,
    };
  }
  return { report, content, filename };
}

// ---------- 脱敏日志 ----------
// 只记录 event / status / reason_code / section_count / item_count，绝不含正文、案号、
// note、tag、title、query。
export type ReportTemplateLog = {
  event: "report_template_action";
  status: ReportStatus;
  reason_code: ReportReasonCode | null;
  section_count: number;
  item_count: number;
};

export function buildReportLog(report: ReportTemplate): ReportTemplateLog {
  return {
    event: "report_template_action",
    status: report.report_status,
    reason_code: report.degrade_reason,
    section_count: report.sections.length,
    item_count: report.item_count > 0 ? Math.round(report.item_count) : 0,
  };
}

function defaultReportLogger(payload: ReportTemplateLog): void {
  if (typeof console === "undefined" || typeof console.info !== "function") {
    return;
  }
  console.info(JSON.stringify(payload));
}

export function logReportTemplate(
  report: ReportTemplate,
  logger: (payload: ReportTemplateLog) => void = defaultReportLogger
): void {
  try {
    logger(buildReportLog(report));
  } catch {
    // 日志绝不破坏报告 / 主链路。
  }
}

export const REPORT_REASON_CODES: readonly ReportReasonCode[] = [
  "list_not_found",
  "empty_list",
  "render_failed",
  "assembly_failed",
  "degraded_overview_only",
  "download_unavailable",
  "download_failed",
];
