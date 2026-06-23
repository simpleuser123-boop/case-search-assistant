// M4-5 类案清单导出（F17）。
//
// 隐私边界（M4-1 合同 / 止损线）：
//   - 导出文件**只含元数据、来源链接/锚点、用户自填备注**：案号 case_number、
//     法院 court、审级 trial_level、案由 case_cause、裁判日期 judgment_date、
//     来源链接/锚点 source_anchor、用户自填 note / tag。
//   - 绝不导出裁判文书正文、摘要正文、要旨正文、chunk 正文、原始案情，也不导出
//     原始 query 或任何自由长文本。
//   - 绝不导出胜负概率、查全率、「已查全 / 保证无遗漏」等绝对覆盖话术，或任何
//     确定性法律结论。
//   - 导出仅在浏览器本地生成下载，不上送后端持久层；导出行为绝不影响主结果排序、
//     召回或 source selection。
//   - 文件头部强制包含数据覆盖与免责说明（不承诺已查全、提示需人工复核）。
//   - 日志只记录 format / count / status / reason_code，绝不含正文、案号、note、tag、title。
//
// 本模块为纯函数 + 可注入下载器，便于在无真实浏览器下单测。所有读写都包了
// try/catch：导出异常只做安全降级（返回 status=failed + reason_code），绝不抛出、
// 绝不破坏主检索 / 阅读 / 清单链路。

import type { CaseListItem, CaseListRecord } from "./caseList";

// 导出格式。仅文本型结构化格式，便于线下整理与交付。
export type ExportFormat = "markdown" | "csv";

// 导出列白名单。严格限定为元数据 / 来源锚点 / 用户自填短字段，
// 与 M4-1 合同「允许持久化 / 可导出」字段集一致。绝不含任何正文型列。
export type ExportField =
  | "case_number"
  | "court"
  | "trial_level"
  | "case_cause"
  | "judgment_date"
  | "source_anchor"
  | "note"
  | "tag";

export const EXPORT_FIELD_WHITELIST: readonly ExportField[] = [
  "case_number",
  "court",
  "trial_level",
  "case_cause",
  "judgment_date",
  "source_anchor",
  "note",
  "tag",
];

// 每个白名单列的中文表头（用于 markdown 表头与 csv 首行）。
export const EXPORT_FIELD_LABELS: Record<ExportField, string> = {
  case_number: "案号",
  court: "法院",
  trial_level: "审级",
  case_cause: "案由",
  judgment_date: "裁判日期",
  source_anchor: "来源引用",
  note: "备注",
  tag: "标签",
};

export type ExportStatus = "exported" | "empty" | "degraded" | "failed";

export type ExportReasonCode =
  | "list_not_found"
  | "empty_list"
  | "no_fields"
  | "unsupported_format"
  | "download_unavailable"
  | "download_failed";

// 一次导出的结构化描述（对应合同 case_list_export）。只含引用 / 配置 / 状态，无正文。
export type CaseListExportDescriptor = {
  export_id: string;
  list_id: string;
  export_format: ExportFormat;
  // fields: 实际写入文件的导出列（白名单子集）。
  fields: ExportField[];
  export_status: ExportStatus;
  // degrade_reason: 非成功态的原因码；成功时为 null。
  degrade_reason: ExportReasonCode | null;
  // item_count: 导出的清单项数（仅计数，不含任何内容）。
  item_count: number;
  created_at: string;
};

// 注入式下载器抽象。生产用基于 Blob + a[download] 的浏览器实现；测试可传内存实现。
export type DownloaderLike = (file: {
  filename: string;
  content: string;
  mimeType: string;
}) => void;

function nowIso(): string {
  return new Date().toISOString();
}

function clean(value: string | null | undefined): string {
  return (value || "").trim();
}

// 生成导出 id：优先 crypto.randomUUID，回退时间戳 + 随机串。仅本地标识，无语义。
function generateExportId(): string {
  try {
    if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
      return `export_${crypto.randomUUID()}`;
    }
  } catch {
    // 忽略，走回退。
  }
  return `export_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`;
}

// ---------- 免责 / 数据覆盖说明（强制写入文件头部）----------

// 文件头部的数据覆盖与免责说明。措辞严格规避绝对话术与诉讼结果判断：
//   - 不出现「已查全 / 保证无遗漏 / 查全率」等覆盖承诺；
//   - 不出现胜诉 / 败诉概率或确定性法律结论；
//   - 明确提示结果可能不完整、需人工复核。
export const EXPORT_DISCLAIMER_LINES: readonly string[] = [
  "本文件由「类案检索助手」导出，仅包含案例元数据、来源引用与用户自填备注，不含裁判文书正文或原始案情。",
  "数据覆盖说明：本清单为检索与人工筛选的阶段性结果，可能存在未覆盖的案例，不代表对相关案件的完整检索。",
  "本文件不对案件检索的完整性作出承诺，亦不提供胜诉或败诉等诉讼结果判断及确定性法律结论。",
  "使用前请结合权威数据库与原始裁判文书进行人工复核，并自行判断案例的相关性与适用性。",
];

// 校验单行文本是否含禁用绝对话术 / 诉讼结果判断。供测试与运行时自检使用。
// 命中即视为越线（应拦截 / NO_GO）。
const FORBIDDEN_EXPORT_PHRASES: readonly string[] = [
  "已查全",
  "查全率",
  "保证无遗漏",
  "无遗漏",
  "确保覆盖",
  "胜诉概率",
  "败诉概率",
  "胜诉率",
  "败诉率",
  "必然胜诉",
  "必然败诉",
];

export function containsForbiddenExportPhrase(text: string): boolean {
  const haystack = text || "";
  return FORBIDDEN_EXPORT_PHRASES.some((phrase) => haystack.includes(phrase));
}

// ---------- 单元格取值（只读白名单字段，绝不读正文）----------

// 从清单项取某个导出列的纯文本值。source_anchor 拼为「case_id#chunk_id」引用串，
// 不含任何正文。任何未知列返回空串（防御）。
export function exportFieldValue(item: CaseListItem, field: ExportField): string {
  switch (field) {
    case "case_number":
      return clean(item.case_number);
    case "court":
      return clean(item.court);
    case "trial_level":
      return clean(item.trial_level);
    case "case_cause":
      return clean(item.case_cause);
    case "judgment_date":
      return clean(item.judgment_date);
    case "source_anchor":
      return (item.source_anchors || [])
        .map((a) => `${clean(a.case_id)}#${clean(a.source_chunk_id)}`)
        .filter((s) => s !== "#")
        .join(" ; ");
    case "note":
      return clean(item.note);
    case "tag":
      return clean(item.tag);
    default:
      return "";
  }
}

// 规整请求的导出列：只保留白名单列、去重、保持白名单声明顺序。空 / 非法输入回退到全列。
export function resolveExportFields(requested?: ExportField[] | null): ExportField[] {
  if (!requested || requested.length === 0) {
    return [...EXPORT_FIELD_WHITELIST];
  }
  const allowed = new Set<ExportField>();
  for (const field of requested) {
    if ((EXPORT_FIELD_WHITELIST as readonly string[]).includes(field)) {
      allowed.add(field);
    }
  }
  if (allowed.size === 0) {
    return [...EXPORT_FIELD_WHITELIST];
  }
  return EXPORT_FIELD_WHITELIST.filter((field) => allowed.has(field));
}

// ---------- 格式化 helper（CSV / Markdown）----------

// CSV 单元格转义：含逗号 / 引号 / 换行时用双引号包裹并转义内部引号。
// 同时把换行折叠为空格，避免用户备注里的换行破坏行结构。
export function escapeCsvCell(value: string): string {
  const collapsed = (value || "").replace(/\r?\n/g, " ");
  if (/[",\n]/.test(collapsed)) {
    return `"${collapsed.replace(/"/g, '""')}"`;
  }
  return collapsed;
}

// Markdown 表格单元格转义：转义竖线、折叠换行，防止破坏表格结构。
export function escapeMarkdownCell(value: string): string {
  return (value || "").replace(/\r?\n/g, " ").replace(/\|/g, "\\|");
}

// 把免责说明渲染为注释行前缀（CSV 用 `# ` 前缀，避免被当作数据行）。
function csvDisclaimerBlock(): string {
  return EXPORT_DISCLAIMER_LINES.map((line) => `# ${line}`).join("\n");
}

// 生成 CSV 文本：头部免责注释 + 表头行 + 数据行。仅白名单列，绝无正文。
export function generateCsv(list: CaseListRecord, fields: ExportField[]): string {
  const header = fields.map((field) => escapeCsvCell(EXPORT_FIELD_LABELS[field])).join(",");
  const rows = list.items.map((item) =>
    fields.map((field) => escapeCsvCell(exportFieldValue(item, field))).join(",")
  );
  return [csvDisclaimerBlock(), "", header, ...rows].join("\n");
}

// 生成 Markdown 文本：标题 + 免责区块（引用块）+ 表格。仅白名单列，绝无正文。
export function generateMarkdown(list: CaseListRecord, fields: ExportField[]): string {
  const title = clean(list.list_title) || "未命名类案清单";
  const lines: string[] = [];
  lines.push(`# ${escapeMarkdownCell(title)}`);
  lines.push("");
  for (const line of EXPORT_DISCLAIMER_LINES) {
    lines.push(`> ${line}`);
  }
  lines.push("");
  lines.push(`共 ${list.items.length} 条案例引用。`);
  lines.push("");
  const headerCells = fields.map((field) => escapeMarkdownCell(EXPORT_FIELD_LABELS[field]));
  lines.push(`| ${headerCells.join(" | ")} |`);
  lines.push(`| ${fields.map(() => "---").join(" | ")} |`);
  for (const item of list.items) {
    const cells = fields.map((field) => escapeMarkdownCell(exportFieldValue(item, field)) || "—");
    lines.push(`| ${cells.join(" | ")} |`);
  }
  return lines.join("\n");
}

function mimeFor(format: ExportFormat): string {
  return format === "csv" ? "text/csv;charset=utf-8" : "text/markdown;charset=utf-8";
}

function extensionFor(format: ExportFormat): string {
  return format === "csv" ? "csv" : "md";
}

// 生成下载文件名：用清单标题的安全化串 + 时间戳，避免路径分隔符 / 特殊字符。
export function buildExportFilename(list: CaseListRecord, format: ExportFormat): string {
  const rawTitle = clean(list.list_title) || "case-list";
  const safe = rawTitle.replace(/[\\/:*?"<>|]+/g, "_").replace(/\s+/g, "_").slice(0, 40);
  const stamp = nowIso().replace(/[:.]/g, "-");
  return `${safe || "case-list"}-${stamp}.${extensionFor(format)}`;
}

// 生成导出文件文本（不触发下载，便于测试 / 复核内容）。format 非法回退 markdown。
export function generateExportContent(
  list: CaseListRecord,
  format: ExportFormat,
  fields: ExportField[]
): string {
  if (format === "csv") {
    return generateCsv(list, fields);
  }
  return generateMarkdown(list, fields);
}

// ---------- 浏览器下载器 ----------

// 默认下载器：基于 Blob + a[download] + objectURL。任一 API 缺失（SSR / 旧环境）
// 直接抛出，由 exportCaseList 捕获并降级为 download_unavailable。
export function browserDownloader(file: {
  filename: string;
  content: string;
  mimeType: string;
}): void {
  if (
    typeof document === "undefined" ||
    typeof URL === "undefined" ||
    typeof URL.createObjectURL !== "function" ||
    typeof Blob === "undefined"
  ) {
    throw new Error("download_unavailable");
  }
  const blob = new Blob([file.content], { type: file.mimeType });
  const url = URL.createObjectURL(blob);
  try {
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = file.filename;
    anchor.rel = "noopener";
    document.body.appendChild(anchor);
    anchor.click();
    document.body.removeChild(anchor);
  } finally {
    if (typeof URL.revokeObjectURL === "function") {
      URL.revokeObjectURL(url);
    }
  }
}

// 返回可注入的浏览器下载器：环境不支持时返回 null（调用方据此降级）。
export function getBrowserDownloader(): DownloaderLike | null {
  try {
    if (typeof document === "undefined" || typeof URL === "undefined" || typeof Blob === "undefined") {
      return null;
    }
    return browserDownloader;
  } catch {
    return null;
  }
}

// ---------- 导出主入口 ----------

export type ExportCaseListResult = {
  descriptor: CaseListExportDescriptor;
  // content: 实际生成的文件文本（成功 / 降级时为字符串；彻底失败为 null）。
  content: string | null;
  filename: string | null;
};

// 把一张清单导出为文件并触发下载。任何异常都安全降级为 failed + reason_code，
// 绝不抛出、绝不影响主链路。只写白名单列，强制带免责说明。
export function exportCaseList(
  list: CaseListRecord | null | undefined,
  options: {
    format?: ExportFormat;
    fields?: ExportField[] | null;
    downloader?: DownloaderLike | null;
  } = {}
): ExportCaseListResult {
  const format: ExportFormat = options.format === "csv" ? "csv" : "markdown";
  const fields = resolveExportFields(options.fields);
  const baseDescriptor = (
    status: ExportStatus,
    reason: ExportReasonCode | null,
    itemCount: number
  ): CaseListExportDescriptor => ({
    export_id: generateExportId(),
    list_id: clean(list?.list_id),
    export_format: format,
    fields,
    export_status: status,
    degrade_reason: reason,
    item_count: itemCount,
    created_at: nowIso(),
  });

  if (!list || !clean(list.list_id)) {
    return { descriptor: baseDescriptor("failed", "list_not_found", 0), content: null, filename: null };
  }
  if (!list.items || list.items.length === 0) {
    return { descriptor: baseDescriptor("empty", "empty_list", 0), content: null, filename: null };
  }

  let content: string;
  try {
    content = generateExportContent(list, format, fields);
  } catch {
    return {
      descriptor: baseDescriptor("failed", "download_failed", list.items.length),
      content: null,
      filename: null,
    };
  }

  const filename = buildExportFilename(list, format);
  const downloader =
    options.downloader === undefined ? getBrowserDownloader() : options.downloader;

  if (!downloader) {
    // 内容已生成但环境无法下载：返回内容（调用方可改用复制 / 预览），状态降级。
    return {
      descriptor: baseDescriptor("degraded", "download_unavailable", list.items.length),
      content,
      filename,
    };
  }

  try {
    downloader({ filename, content, mimeType: mimeFor(format) });
  } catch {
    return {
      descriptor: baseDescriptor("degraded", "download_failed", list.items.length),
      content,
      filename,
    };
  }

  return { descriptor: baseDescriptor("exported", null, list.items.length), content, filename };
}

// ---------- 脱敏日志 ----------
// 只记录 event / format / status / reason_code / count，绝不含正文、案号、note、tag、title。
export type CaseListExportLog = {
  event: "case_list_export";
  format: ExportFormat;
  status: ExportStatus;
  reason_code: ExportReasonCode | null;
  count: number;
};

export function buildCaseListExportLog(
  descriptor: CaseListExportDescriptor
): CaseListExportLog {
  return {
    event: "case_list_export",
    format: descriptor.export_format,
    status: descriptor.export_status,
    reason_code: descriptor.degrade_reason,
    count: descriptor.item_count > 0 ? Math.round(descriptor.item_count) : 0,
  };
}

function defaultExportLogger(payload: CaseListExportLog): void {
  if (typeof console === "undefined" || typeof console.info !== "function") {
    return;
  }
  console.info(JSON.stringify(payload));
}

export function logCaseListExport(
  descriptor: CaseListExportDescriptor,
  logger: (payload: CaseListExportLog) => void = defaultExportLogger
): void {
  const payload = buildCaseListExportLog(descriptor);
  try {
    logger(payload);
  } catch {
    // 日志绝不破坏导出 / 主链路。
  }
}

export const CASE_LIST_EXPORT_REASON_CODES: readonly ExportReasonCode[] = [
  "list_not_found",
  "empty_list",
  "no_fields",
  "unsupported_format",
  "download_unavailable",
  "download_failed",
];
