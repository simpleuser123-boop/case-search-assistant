import { describe, expect, it } from "vitest";

import type { CaseListItem, CaseListRecord } from "./caseList";
import {
  buildReportLog,
  buildReportTemplate,
  downloadReport,
  filterAnchoredFragments,
  REPORT_DISCLAIMER_LINES,
  REPORT_REASON_CODES,
  REPORT_REVIEW_POINTS,
  renderReportMarkdown,
  reportContainsForbiddenPhrase,
  reportRenderHasForbiddenPhrase,
  sanitizeReportAnchors,
  type ReportAnchoredFragment,
  type ReportSection,
  type ReportTemplate,
} from "./reportTemplate";
import type { DownloaderLike } from "./caseListExport";

function makeItem(overrides: Partial<CaseListItem> = {}): CaseListItem {
  const base: CaseListItem = {
    case_id: "case-1",
    case_number: "（2021）京01民终123号",
    court: "北京市第一中级人民法院",
    trial_level: "二审",
    case_cause: "房屋买卖合同纠纷",
    judgment_date: "2021-06-01",
    source_anchors: [
      { case_id: "case-1", source_chunk_id: "chunk-9", anchor_type: "case_record", chunk_type: null },
    ],
    note: "重点关注违约金认定",
    tag: "违约金",
    added_at: "2026-06-14T00:00:00.000Z",
  };
  return { ...base, ...overrides };
}

function makeList(overrides: Partial<CaseListRecord> = {}): CaseListRecord {
  return {
    list_id: "list-1",
    list_title: "买卖合同类案",
    items: [makeItem()],
    created_at: "2026-06-14T00:00:00.000Z",
    updated_at: "2026-06-14T00:00:00.000Z",
    list_status: "active",
    ...overrides,
  };
}

function memoryDownloader() {
  const calls: Array<{ filename: string; content: string; mimeType: string }> = [];
  const fn: DownloaderLike = (file) => void calls.push(file);
  return { fn, calls };
}

function sectionOf<K extends ReportSection["kind"]>(
  report: ReportTemplate,
  kind: K
): Extract<ReportSection, { kind: K }> | undefined {
  return report.sections.find((s) => s.kind === kind) as
    | Extract<ReportSection, { kind: K }>
    | undefined;
}

// 正文型禁词集合：报告任何产物都不得出现这些字段名 / 内容。
const BODY_MARKERS = [
  "case_fact_body",
  "candidate_body",
  "chunk_body",
  "judgment_long_text",
  "本院认为",
  "经审理查明",
];

describe("buildReportTemplate 结构与边界", () => {
  it("正常清单生成完整 5 段骨架，状态 generated", () => {
    const report = buildReportTemplate(makeList(), { backgroundNote: "检索违约金认定标准" });
    expect(report.report_status).toBe("generated");
    expect(report.degrade_reason).toBeNull();
    expect(report.item_count).toBe(1);
    const kinds = report.sections.map((s) => s.kind);
    expect(kinds).toEqual([
      "search_background",
      "list_overview",
      "case_entries",
      "review_points",
      "disclaimer",
    ]);
  });

  it("逐案条目只含元数据 / 锚点 / 用户自填，默认无 AI 片段", () => {
    const report = buildReportTemplate(makeList());
    const entries = sectionOf(report, "case_entries");
    expect(entries?.entries).toHaveLength(1);
    const entry = entries!.entries[0];
    expect(entry.case_number).toBe("（2021）京01民终123号");
    expect(entry.source_anchors).toHaveLength(1);
    expect(entry.anchored_fragments).toEqual([]);
    // 条目对象的键必须落在白名单内，绝无正文键。
    const allowedKeys = new Set([
      "ordinal",
      "case_id",
      "case_number",
      "court",
      "trial_level",
      "case_cause",
      "judgment_date",
      "source_anchors",
      "note",
      "tag",
      "anchored_fragments",
    ]);
    for (const key of Object.keys(entry)) {
      expect(allowedKeys.has(key)).toBe(true);
    }
  });

  it("必含免责说明，且待复核要点为中性动作清单", () => {
    const report = buildReportTemplate(makeList());
    const disclaimer = sectionOf(report, "disclaimer");
    expect(disclaimer?.lines).toEqual([...REPORT_DISCLAIMER_LINES]);
    const review = sectionOf(report, "review_points");
    expect(review?.points).toEqual([...REPORT_REVIEW_POINTS]);
  });

  it("缺清单降级为 failed/list_not_found，仍带免责，不抛出", () => {
    const report = buildReportTemplate(null);
    expect(report.report_status).toBe("failed");
    expect(report.degrade_reason).toBe("list_not_found");
    expect(sectionOf(report, "disclaimer")).toBeDefined();
  });

  it("空清单降级为 failed/empty_list，仍带免责", () => {
    const report = buildReportTemplate(makeList({ items: [] }));
    expect(report.report_status).toBe("failed");
    expect(report.degrade_reason).toBe("empty_list");
    expect(sectionOf(report, "disclaimer")).toBeDefined();
  });
});

describe("锚点与 AI 片段守门", () => {
  it("sanitizeReportAnchors 丢弃跨案 / 缺字段锚点", () => {
    const anchors = sanitizeReportAnchors({
      case_id: "case-1",
      source_anchors: [
        { case_id: "case-1", source_chunk_id: "c1", anchor_type: "case_record", chunk_type: null },
        { case_id: "case-2", source_chunk_id: "c2", anchor_type: "case_record", chunk_type: null },
        { case_id: "case-1", source_chunk_id: "", anchor_type: "x", chunk_type: null },
      ],
    });
    expect(anchors).toHaveLength(1);
    expect(anchors[0].source_chunk_id).toBe("c1");
  });

  it("无锚点片段被丢弃", () => {
    const fragments = [
      { case_id: "case-1", text: "摘录A", source_anchor: { case_id: "", source_chunk_id: "" } },
    ] as ReportAnchoredFragment[];
    const kept = filterAnchoredFragments(fragments, "case-1", new Set(["case-1"]));
    expect(kept).toEqual([]);
  });

  it("跨案锚点（锚点 case_id 与片段不一致）被丢弃", () => {
    const fragments: ReportAnchoredFragment[] = [
      {
        case_id: "case-1",
        text: "摘录B",
        source_anchor: { case_id: "case-2", source_chunk_id: "c9" },
      },
    ];
    const kept = filterAnchoredFragments(fragments, "case-1", new Set(["case-1"]));
    expect(kept).toEqual([]);
  });

  it("含禁用话术的片段被丢弃", () => {
    const fragments: ReportAnchoredFragment[] = [
      {
        case_id: "case-1",
        text: "该案胜诉概率较高",
        source_anchor: { case_id: "case-1", source_chunk_id: "c1" },
      },
    ];
    const kept = filterAnchoredFragments(fragments, "case-1", new Set(["case-1"]));
    expect(kept).toEqual([]);
  });

  it("合法且带锚点的片段保留，并进入报告渲染", () => {
    const fragments: ReportAnchoredFragment[] = [
      {
        case_id: "case-1",
        text: "争议焦点涉及违约金调整",
        source_anchor: { case_id: "case-1", source_chunk_id: "c1", anchor_type: "case_record" },
      },
    ];
    const report = buildReportTemplate(makeList(), { anchoredFragments: fragments });
    const entries = sectionOf(report, "case_entries");
    expect(entries?.entries[0].anchored_fragments).toHaveLength(1);
    const md = renderReportMarkdown(report);
    // 渲染出的 AI 片段必须携带来源锚点串。
    expect(md).toContain("争议焦点涉及违约金调整");
    expect(md).toContain("case-1#c1");
  });

  it("片段归属清单外案例（allowed 不含）被丢弃", () => {
    const fragments: ReportAnchoredFragment[] = [
      {
        case_id: "case-X",
        text: "无关摘录",
        source_anchor: { case_id: "case-X", source_chunk_id: "cX" },
      },
    ];
    const kept = filterAnchoredFragments(fragments, "case-X", new Set(["case-1"]));
    expect(kept).toEqual([]);
  });
});

describe("渲染：免责 / 无正文 / 无禁用话术", () => {
  it("Markdown 含标题、各章节标题与免责引用块", () => {
    const md = renderReportMarkdown(buildReportTemplate(makeList()));
    expect(md).toContain("# 买卖合同类案");
    expect(md).toContain("## 检索背景");
    expect(md).toContain("## 清单概览");
    expect(md).toContain("## 待人工复核要点");
    expect(md).toContain("## 免责说明");
    expect(md).toContain("> 本报告由「类案检索助手」");
  });

  it("注入正文型字段的清单项，渲染结果不泄露任何正文标志", () => {
    const polluted = makeItem();
    // 故意塞入正文型键（模拟被篡改 / 旧数据），构造器只读白名单字段。
    (polluted as unknown as Record<string, unknown>).case_fact_body = "经审理查明，被告...";
    (polluted as unknown as Record<string, unknown>).judgment_long_text = "本院认为，应当...";
    const report = buildReportTemplate(makeList({ items: [polluted] }));
    const md = renderReportMarkdown(report);
    for (const marker of BODY_MARKERS) {
      expect(md.includes(marker)).toBe(false);
    }
  });

  it("报告全文不含禁用绝对话术 / 诉讼结果判断", () => {
    const report = buildReportTemplate(makeList(), { backgroundNote: "检索背景说明" });
    expect(reportRenderHasForbiddenPhrase(report)).toBe(false);
  });

  it("reportContainsForbiddenPhrase 命中胜负 / 查全率话术", () => {
    expect(reportContainsForbiddenPhrase("胜诉率约为八成")).toBe(true);
    expect(reportContainsForbiddenPhrase("查全率 100%")).toBe(true);
    expect(reportContainsForbiddenPhrase("逐案核对案号")).toBe(false);
  });
});

describe("导出与日志降级", () => {
  it("正常报告经内存下载器导出 Markdown 文件", () => {
    const report = buildReportTemplate(makeList());
    const { fn, calls } = memoryDownloader();
    const result = downloadReport(report, { downloader: fn });
    expect(calls).toHaveLength(1);
    expect(calls[0].filename.endsWith(".md")).toBe(true);
    expect(calls[0].mimeType).toContain("text/markdown");
    expect(result.content).not.toBeNull();
  });

  it("无下载器时降级 download_unavailable，仍返回内容供预览", () => {
    const report = buildReportTemplate(makeList());
    const result = downloadReport(report, { downloader: null });
    expect(result.report.report_status).toBe("degraded");
    expect(result.report.degrade_reason).toBe("download_unavailable");
    expect(result.content).not.toBeNull();
  });

  it("下载器抛错时降级 download_failed，不抛出", () => {
    const report = buildReportTemplate(makeList());
    const throwing: DownloaderLike = () => {
      throw new Error("boom");
    };
    const result = downloadReport(report, { downloader: throwing });
    expect(result.report.report_status).toBe("degraded");
    expect(result.report.degrade_reason).toBe("download_failed");
  });

  it("failed 报告不生成下载文件", () => {
    const report = buildReportTemplate(null);
    const { fn, calls } = memoryDownloader();
    const result = downloadReport(report, { downloader: fn });
    expect(calls).toHaveLength(0);
    expect(result.content).toBeNull();
  });

  it("日志只含状态 / reason / 计数，无正文键", () => {
    const report = buildReportTemplate(makeList());
    const log = buildReportLog(report);
    expect(log.event).toBe("report_template_action");
    expect(log.status).toBe("generated");
    expect(log.item_count).toBe(1);
    expect(Object.keys(log)).toEqual([
      "event",
      "status",
      "reason_code",
      "section_count",
      "item_count",
    ]);
    const serialized = JSON.stringify(log);
    expect(serialized).not.toContain("买卖合同");
    expect(serialized).not.toContain("违约金");
  });

  it("REPORT_REASON_CODES 覆盖全部降级原因码", () => {
    expect(REPORT_REASON_CODES).toContain("list_not_found");
    expect(REPORT_REASON_CODES).toContain("empty_list");
    expect(REPORT_REASON_CODES).toContain("download_unavailable");
    expect(REPORT_REASON_CODES).toContain("download_failed");
  });
});
