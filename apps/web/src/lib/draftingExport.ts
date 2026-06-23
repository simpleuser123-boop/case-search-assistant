// E6-4 文书工作台 DraftDescriptor 导出（复用 M4-5 清单导出免责头机制）。
//
// 隐私 / 边界红线（E6「只组装锚定来源、不起草结论」+ M4-5 导出边界）：
//   - 导出文件**只含元数据、来源链接/锚点、用户自填备注**：
//     · structure_skeleton 段落标题（仅标题，非正文）；
//     · 类案引用 CandidateRef 白名单元数据（案号/法院/审级/案由/裁判日期）+ 来源锚点；
//     · 法条引用 StatuteRef 白名单元数据（法律名称/条号）+ 来源锚点（text_id）；
//     · 用户自填 note / tag。
//   - 绝不导出裁判文书正文、摘要正文、要旨正文、chunk 正文、起草正文、原始案情，
//     也不导出原始 query 或任何自由长文本；structure_skeleton 仍是标题不含正文。
//   - 绝不导出胜负概率、查全率、「已查全 / 保证无遗漏」等绝对覆盖话术，或任何
//     确定性法律结论 / 裁判结果预测。
//   - **无锚点引用不进入导出**（沿用 M4「无来源不进交付物」红线，与 E6-1 sanitize 一致）。
//   - 文件头部**强制注入**数据覆盖说明 + 免责声明（不可关闭、不含个案结论）。
//   - 导出仅在浏览器本地生成下载，不上送后端持久层；导出行为绝不影响主检索 / 文书链路。
//   - 导出 100% 来自已取 DraftDescriptor 的白名单字段，**不重新生成 / 不补全任何文本**。
//   - 日志只记 format / status / reason_code / count，绝不含正文、标题、案号、note、tag、draft_id 全文。
//
// 本模块为纯函数 + 可注入下载器，便于在无真实浏览器下单测。所有读写都包了 try/catch：
// 导出异常只做安全降级（返回 status=failed/degraded + reason_code），绝不抛出、绝不破坏主链路。

import type {
  DraftDescriptorView,
  DraftCandidateRefView,
  DraftStatuteRefView,
} from "../services/draftingApi";

// 导出格式。仅文本型结构化格式，便于线下整理与交付。
export type DraftExportFormat = "markdown" | "text";

export type DraftExportStatus = "exported" | "empty" | "degraded" | "failed";

export type DraftExportReasonCode =
  | "draft_not_found"
  | "empty_skeleton"
  | "unsupported_format"
  | "download_unavailable"
  | "download_failed";

// 一次导出的结构化描述（只含引用 / 配置 / 状态计数，无任何正文 / 标题）。
export type DraftExportDescriptor = {
  export_id: string;
  draft_id: string;
  export_format: DraftExportFormat;
  export_status: DraftExportStatus;
  degrade_reason: DraftExportReasonCode | null;
  // 计数字段：仅计数，不含任何内容。
  skeleton_count: number;
  candidate_count: number;
  statute_count: number;
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
      return `draft_export_${crypto.randomUUID()}`;
    }
  } catch {
    // 忽略，走回退。
  }
  return `draft_export_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`;
}

// ---------- 免责 / 数据覆盖说明（强制写入文件头部，不可关闭）----------

// 文件头部的数据覆盖与免责说明。措辞与 M4-5 同口径，严格规避绝对话术与诉讼结果判断：
//   - 不出现「已查全 / 保证无遗漏 / 查全率」等覆盖承诺；
//   - 不出现胜诉 / 败诉概率或确定性法律结论；
//   - 明确提示本文件为检索沉淀骨架、不构成法律意见、不预测裁判结果、需人工复核。
export const DRAFT_EXPORT_DISCLAIMER_LINES: readonly string[] = [
  "本文件由「类案检索助手」文书工作台导出，仅包含文书结构骨架（段落标题）、案例/法条引用的元数据与来源锚点、以及用户自填备注，不含裁判文书正文、原始案情或起草正文。",
  "数据覆盖说明：底层语料以刑事裁判文书与相关法条为主，本骨架为检索与人工筛选的阶段性沉淀，可能存在未覆盖的案例与法条，不代表对相关问题的完整检索。",
  "本文件不对检索的完整性作出承诺，亦不提供胜诉或败诉等诉讼结果判断、裁判结果预测及确定性法律结论；本文件为检索沉淀骨架，不构成法律意见。",
  "使用前请结合权威数据库与原始裁判文书、现行有效法条进行人工复核，并自行判断引用的相关性与适用性。",
];

// 校验单行文本是否含禁用绝对话术 / 诉讼结果判断。供测试与运行时自检使用。
// 命中即视为越线（应拦截 / NO_GO）。与 M4-5 FORBIDDEN_EXPORT_PHRASES 同口径。
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
  "胜诉可能性",
  "败诉可能性",
];

export function containsForbiddenExportPhrase(text: string): boolean {
  const haystack = text || "";
  return FORBIDDEN_EXPORT_PHRASES.some((phrase) => haystack.includes(phrase));
}

// ---------- 锚点判定（与 draftingApi 同口径，无锚点不进导出）----------

// 类案引用是否有有效锚点：至少一个带 case_id + source_chunk_id 的锚点。
export function candidateRefHasAnchor(ref: DraftCandidateRefView): boolean {
  return (
    Array.isArray(ref?.source_anchors) &&
    ref.source_anchors.some(
      (a) =>
        typeof a?.case_id === "string" &&
        a.case_id.trim().length > 0 &&
        typeof a?.source_chunk_id === "string" &&
        a.source_chunk_id.trim().length > 0,
    )
  );
}

// 法条引用是否有有效锚点：至少一个带非空 text_id 的锚点。
export function statuteRefHasAnchor(ref: DraftStatuteRefView): boolean {
  return (
    Array.isArray(ref?.statute_anchors) &&
    ref.statute_anchors.some((a) => typeof a?.text_id === "string" && a.text_id.trim().length > 0)
  );
}

// 把类案锚点拼为「case_id#chunk_id」引用串（不含任何正文）。
function candidateAnchorText(ref: DraftCandidateRefView): string {
  return (ref.source_anchors || [])
    .map((a) => `${clean(a.case_id)}#${clean(a.source_chunk_id)}`)
    .filter((s) => s !== "#")
    .join(" ; ");
}

// 把法条锚点拼为 text_id 引用串（不含任何条文正文）。
function statuteAnchorText(ref: DraftStatuteRefView): string {
  return (ref.statute_anchors || [])
    .map((a) => clean(a.text_id))
    .filter((s) => s.length > 0)
    .join(" ; ");
}

// ---------- 引用收敛（只取白名单字段 + 锚点，丢无锚点引用）----------

// 收敛后的类案引用行（导出用，零正文）。
type ExportCandidateRow = {
  case_id: string;
  case_number: string;
  court: string;
  trial_level: string;
  case_cause: string;
  judgment_date: string;
  anchor: string;
};

// 收敛后的法条引用行（导出用，零条文正文 —— 注意：不取 article_text）。
type ExportStatuteRow = {
  statute_id: string;
  law_name: string;
  article_no: string;
  anchor: string;
};

// 从 DraftDescriptor 取**带锚点**的类案引用行（无锚点丢弃；只读白名单元数据 + 锚点）。
export function collectCandidateRows(draft: DraftDescriptorView): ExportCandidateRow[] {
  return (draft.candidate_refs || [])
    .filter(candidateRefHasAnchor)
    .map((ref) => ({
      case_id: clean(ref.case_id),
      case_number: clean(ref.case_number),
      court: clean(ref.court),
      trial_level: clean(ref.trial_level),
      case_cause: clean(ref.case_cause),
      judgment_date: clean(ref.judgment_date),
      anchor: candidateAnchorText(ref),
    }));
}

// 从 DraftDescriptor 取**带锚点**的法条引用行（无锚点丢弃；只读白名单元数据 + 锚点）。
// 红线：**绝不读取 article_text**（即便后端回填语料条文，导出也只携带元数据 + 锚点，
// 由用户回到法条页核验条文，避免正文沉淀进导出文件）。
export function collectStatuteRows(draft: DraftDescriptorView): ExportStatuteRow[] {
  return (draft.statute_refs || [])
    .filter(statuteRefHasAnchor)
    .map((ref) => ({
      statute_id: clean(ref.statute_id),
      law_name: clean(ref.law_name),
      article_no: clean(ref.article_no),
      anchor: statuteAnchorText(ref),
    }));
}

// 段落标题清单：去空白、丢空项（仍是标题，不含正文）。
export function collectSkeletonTitles(draft: DraftDescriptorView): string[] {
  return (draft.structure_skeleton || [])
    .map((t) => clean(t))
    .filter((t) => t.length > 0);
}

// ---------- 格式化 helper（Markdown / 纯文本）----------

// Markdown 表格单元格转义：转义竖线、折叠换行，防止破坏表格结构。
export function escapeMarkdownCell(value: string): string {
  return (value || "").replace(/\r?\n/g, " ").replace(/\|/g, "\\|");
}

const CANDIDATE_HEADERS = ["案号", "法院", "审级", "案由", "裁判日期", "来源锚点"] as const;
const STATUTE_HEADERS = ["法律名称", "条号", "来源锚点"] as const;

function candidateCells(row: ExportCandidateRow): string[] {
  return [
    row.case_number || row.case_id || "—",
    row.court || "—",
    row.trial_level || "—",
    row.case_cause || "—",
    row.judgment_date || "—",
    row.anchor || "—",
  ];
}

function statuteCells(row: ExportStatuteRow): string[] {
  return [row.law_name || row.statute_id || "—", row.article_no || "—", row.anchor || "—"];
}

// 生成 Markdown 文本：免责区块（引用块）+ 结构骨架（标题清单）+ 类案引用表 + 法条引用表 + 备注。
// 仅白名单字段，绝无正文 / 结论 / 胜负话术。
export function generateDraftMarkdown(
  draft: DraftDescriptorView,
  skeleton: string[],
  candidates: ExportCandidateRow[],
  statutes: ExportStatuteRow[],
): string {
  const lines: string[] = [];
  lines.push("# 文书结构骨架（检索沉淀）");
  lines.push("");
  for (const line of DRAFT_EXPORT_DISCLAIMER_LINES) {
    lines.push(`> ${line}`);
  }
  lines.push("");

  lines.push("## 结构骨架（段落标题）");
  lines.push("");
  skeleton.forEach((title, index) => {
    lines.push(`${index + 1}. ${escapeMarkdownCell(title)}`);
  });
  lines.push("");

  lines.push(`## 参考类案（${candidates.length} 条，均带来源锚点）`);
  lines.push("");
  if (candidates.length > 0) {
    lines.push(`| ${CANDIDATE_HEADERS.join(" | ")} |`);
    lines.push(`| ${CANDIDATE_HEADERS.map(() => "---").join(" | ")} |`);
    for (const row of candidates) {
      lines.push(`| ${candidateCells(row).map(escapeMarkdownCell).join(" | ")} |`);
    }
  } else {
    lines.push("（无带锚点的类案引用）");
  }
  lines.push("");

  lines.push(`## 参考法条（${statutes.length} 条，均带来源锚点）`);
  lines.push("");
  if (statutes.length > 0) {
    lines.push(`| ${STATUTE_HEADERS.join(" | ")} |`);
    lines.push(`| ${STATUTE_HEADERS.map(() => "---").join(" | ")} |`);
    for (const row of statutes) {
      lines.push(`| ${statuteCells(row).map(escapeMarkdownCell).join(" | ")} |`);
    }
  } else {
    lines.push("（无带锚点的法条引用）");
  }
  lines.push("");

  const note = clean(draft.note);
  const tag = clean(draft.tag);
  if (note || tag) {
    lines.push("## 用户备注");
    lines.push("");
    if (tag) {
      lines.push(`- 标签：${escapeMarkdownCell(tag)}`);
    }
    if (note) {
      lines.push(`- 备注：${escapeMarkdownCell(note)}`);
    }
    lines.push("");
  }

  return lines.join("\n");
}

// 生成纯文本：免责区块 + 结构骨架 + 引用（以「字段: 值」行展开）+ 备注。仅白名单字段，绝无正文。
export function generateDraftText(
  draft: DraftDescriptorView,
  skeleton: string[],
  candidates: ExportCandidateRow[],
  statutes: ExportStatuteRow[],
): string {
  const lines: string[] = [];
  lines.push("文书结构骨架（检索沉淀）");
  lines.push("");
  for (const line of DRAFT_EXPORT_DISCLAIMER_LINES) {
    lines.push(line);
  }
  lines.push("");

  lines.push("【结构骨架（段落标题）】");
  skeleton.forEach((title, index) => {
    lines.push(`  ${index + 1}. ${title}`);
  });
  lines.push("");

  lines.push(`【参考类案（${candidates.length} 条，均带来源锚点）】`);
  candidates.forEach((row, index) => {
    lines.push(
      `  ${index + 1}. 案号:${row.case_number || row.case_id || "—"}  法院:${row.court || "—"}  ` +
        `审级:${row.trial_level || "—"}  案由:${row.case_cause || "—"}  ` +
        `裁判日期:${row.judgment_date || "—"}  来源锚点:${row.anchor || "—"}`,
    );
  });
  lines.push("");

  lines.push(`【参考法条（${statutes.length} 条，均带来源锚点）】`);
  statutes.forEach((row, index) => {
    lines.push(
      `  ${index + 1}. 法律名称:${row.law_name || row.statute_id || "—"}  ` +
        `条号:${row.article_no || "—"}  来源锚点:${row.anchor || "—"}`,
    );
  });
  lines.push("");

  const note = clean(draft.note);
  const tag = clean(draft.tag);
  if (note || tag) {
    lines.push("【用户备注】");
    if (tag) {
      lines.push(`  标签: ${tag}`);
    }
    if (note) {
      lines.push(`  备注: ${note}`);
    }
    lines.push("");
  }

  return lines.join("\n");
}

function mimeFor(format: DraftExportFormat): string {
  return format === "text" ? "text/plain;charset=utf-8" : "text/markdown;charset=utf-8";
}

function extensionFor(format: DraftExportFormat): string {
  return format === "text" ? "txt" : "md";
}

// 生成下载文件名：用首个段落标题的安全化串 + 时间戳，避免路径分隔符 / 特殊字符。
export function buildDraftExportFilename(
  draft: DraftDescriptorView,
  format: DraftExportFormat,
): string {
  const rawTitle = clean(draft.structure_skeleton?.[0]) || "drafting-skeleton";
  const safe = rawTitle.replace(/[\\/:*?"<>|]+/g, "_").replace(/\s+/g, "_").slice(0, 40);
  const stamp = nowIso().replace(/[:.]/g, "-");
  return `${safe || "drafting-skeleton"}-${stamp}.${extensionFor(format)}`;
}

// 生成导出文件文本（不触发下载，便于测试 / 复核内容）。format 非法回退 markdown。
export function generateDraftExportContent(
  draft: DraftDescriptorView,
  format: DraftExportFormat,
): string {
  const skeleton = collectSkeletonTitles(draft);
  const candidates = collectCandidateRows(draft);
  const statutes = collectStatuteRows(draft);
  if (format === "text") {
    return generateDraftText(draft, skeleton, candidates, statutes);
  }
  return generateDraftMarkdown(draft, skeleton, candidates, statutes);
}

// ---------- 浏览器下载器（与 M4-5 同款，Blob + a[download]）----------

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

export type ExportDraftResult = {
  descriptor: DraftExportDescriptor;
  // content: 实际生成的文件文本（成功 / 降级时为字符串；彻底失败为 null）。
  content: string | null;
  filename: string | null;
};

// 把一份 DraftDescriptor 导出为文件并触发下载。任何异常都安全降级为 failed/degraded +
// reason_code，绝不抛出、绝不影响主链路。只写白名单字段，强制带免责说明，无锚点引用不进导出。
export function exportDraft(
  draft: DraftDescriptorView | null | undefined,
  options: {
    format?: DraftExportFormat;
    downloader?: DownloaderLike | null;
  } = {},
): ExportDraftResult {
  const format: DraftExportFormat = options.format === "text" ? "text" : "markdown";

  const baseDescriptor = (
    status: DraftExportStatus,
    reason: DraftExportReasonCode | null,
    counts: { skeleton: number; candidate: number; statute: number },
  ): DraftExportDescriptor => ({
    export_id: generateExportId(),
    draft_id: clean(draft?.draft_id),
    export_format: format,
    export_status: status,
    degrade_reason: reason,
    skeleton_count: counts.skeleton,
    candidate_count: counts.candidate,
    statute_count: counts.statute,
    created_at: nowIso(),
  });

  if (!draft || !clean(draft.draft_id)) {
    return {
      descriptor: baseDescriptor("failed", "draft_not_found", {
        skeleton: 0,
        candidate: 0,
        statute: 0,
      }),
      content: null,
      filename: null,
    };
  }

  const skeleton = collectSkeletonTitles(draft);
  const candidates = collectCandidateRows(draft);
  const statutes = collectStatuteRows(draft);
  const counts = {
    skeleton: skeleton.length,
    candidate: candidates.length,
    statute: statutes.length,
  };

  // 空骨架视为无可导出内容（仍提示，不抛错）。
  if (skeleton.length === 0) {
    return {
      descriptor: baseDescriptor("empty", "empty_skeleton", counts),
      content: null,
      filename: null,
    };
  }

  let content: string;
  try {
    content = generateDraftExportContent(draft, format);
  } catch {
    return {
      descriptor: baseDescriptor("failed", "download_failed", counts),
      content: null,
      filename: null,
    };
  }

  const filename = buildDraftExportFilename(draft, format);
  const downloader =
    options.downloader === undefined ? getBrowserDownloader() : options.downloader;

  if (!downloader) {
    // 内容已生成但环境无法下载：返回内容（调用方可改用复制 / 预览），状态降级。
    return {
      descriptor: baseDescriptor("degraded", "download_unavailable", counts),
      content,
      filename,
    };
  }

  try {
    downloader({ filename, content, mimeType: mimeFor(format) });
  } catch {
    return {
      descriptor: baseDescriptor("degraded", "download_failed", counts),
      content,
      filename,
    };
  }

  return { descriptor: baseDescriptor("exported", null, counts), content, filename };
}

// ---------- 脱敏日志 ----------
// 只记录 event / format / status / reason_code / 计数，绝不含正文、标题、案号、note、tag、draft_id 全文。
export type DraftExportLog = {
  event: "drafting_export";
  format: DraftExportFormat;
  status: DraftExportStatus;
  reason_code: DraftExportReasonCode | null;
  skeleton_count: number;
  candidate_count: number;
  statute_count: number;
};

export function buildDraftExportLog(descriptor: DraftExportDescriptor): DraftExportLog {
  const safeCount = (n: number) => (Number.isFinite(n) && n > 0 ? Math.round(n) : 0);
  return {
    event: "drafting_export",
    format: descriptor.export_format,
    status: descriptor.export_status,
    reason_code: descriptor.degrade_reason,
    skeleton_count: safeCount(descriptor.skeleton_count),
    candidate_count: safeCount(descriptor.candidate_count),
    statute_count: safeCount(descriptor.statute_count),
  };
}

function defaultExportLogger(payload: DraftExportLog): void {
  if (typeof console === "undefined" || typeof console.info !== "function") {
    return;
  }
  console.info(JSON.stringify(payload));
}

export function logDraftExport(
  descriptor: DraftExportDescriptor,
  logger: (payload: DraftExportLog) => void = defaultExportLogger,
): void {
  const payload = buildDraftExportLog(descriptor);
  try {
    logger(payload);
  } catch {
    // 日志绝不破坏导出 / 主链路。
  }
}

export const DRAFT_EXPORT_REASON_CODES: readonly DraftExportReasonCode[] = [
  "draft_not_found",
  "empty_skeleton",
  "unsupported_format",
  "download_unavailable",
  "download_failed",
];
