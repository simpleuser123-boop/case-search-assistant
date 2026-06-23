import { describe, expect, it, vi } from "vitest";

import type { CaseListItem, CaseListRecord } from "./caseList";
import {
  buildCaseListExportLog,
  buildExportFilename,
  CASE_LIST_EXPORT_REASON_CODES,
  containsForbiddenExportPhrase,
  escapeCsvCell,
  escapeMarkdownCell,
  EXPORT_DISCLAIMER_LINES,
  EXPORT_FIELD_LABELS,
  EXPORT_FIELD_WHITELIST,
  exportCaseList,
  exportFieldValue,
  generateCsv,
  generateExportContent,
  generateMarkdown,
  logCaseListExport,
  resolveExportFields,
  type DownloaderLike,
  type ExportField,
} from "./caseListExport";

// 一个同时携带「正文型」字段的清单项，导出器必须只读白名单字段、绝不输出正文。
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

// 注入式内存下载器：记录被下载的文件，便于断言内容。
function memoryDownloader() {
  const calls: Array<{ filename: string; content: string; mimeType: string }> = [];
  const fn: DownloaderLike = (file) => void calls.push(file);
  return { fn, calls };
}

// 模拟正文，断言绝不出现在任何导出文件中。
const BODY_MARKERS = [
  "判决如下",
  "本院认为",
  "经审理查明",
  "原告诉称",
  "上诉人不服",
];

describe("caseListExport 白名单与边界", () => {
  it("白名单只含元数据 / 来源引用 / 用户自填短字段，无任何正文型列", () => {
    expect([...EXPORT_FIELD_WHITELIST].sort()).toEqual(
      [
        "case_cause",
        "case_number",
        "court",
        "judgment_date",
        "note",
        "source_anchor",
        "tag",
        "trial_level",
      ].sort()
    );
    // 不得出现正文型列名。
    const forbiddenCols = [
      "case_fact_body",
      "candidate_body",
      "chunk_body",
      "judgment_long_text",
      "raw_query",
      "summary",
      "holding",
    ];
    for (const col of forbiddenCols) {
      expect(EXPORT_FIELD_WHITELIST as readonly string[]).not.toContain(col);
    }
  });

  it("resolveExportFields 过滤非白名单列、去重、保序；空输入回退全列", () => {
    expect(resolveExportFields(["court", "case_number"])).toEqual(["case_number", "court"]);
    expect(
      resolveExportFields(["chunk_body" as ExportField, "note", "note"])
    ).toEqual(["note"]);
    expect(resolveExportFields([])).toEqual([...EXPORT_FIELD_WHITELIST]);
    expect(resolveExportFields(null)).toEqual([...EXPORT_FIELD_WHITELIST]);
    expect(resolveExportFields(["chunk_body" as ExportField])).toEqual([
      ...EXPORT_FIELD_WHITELIST,
    ]);
  });

  it("exportFieldValue 只取白名单字段；source_anchor 拼为引用串不含正文", () => {
    const item = makeItem();
    expect(exportFieldValue(item, "case_number")).toBe("（2021）京01民终123号");
    expect(exportFieldValue(item, "source_anchor")).toBe("case-1#chunk-9");
    expect(exportFieldValue(item, "note")).toBe("重点关注违约金认定");
    // 未知列防御返回空串。
    expect(exportFieldValue(item, "judgment_long_text" as ExportField)).toBe("");
  });
});

describe("免责说明与禁用话术", () => {
  it("免责文案本身不含任何禁用绝对话术 / 诉讼结果判断", () => {
    for (const line of EXPORT_DISCLAIMER_LINES) {
      expect(containsForbiddenExportPhrase(line)).toBe(false);
    }
  });

  it("containsForbiddenExportPhrase 能识别绝对覆盖话术与胜负概率", () => {
    expect(containsForbiddenExportPhrase("本清单已查全所有相关案例")).toBe(true);
    expect(containsForbiddenExportPhrase("查全率 100%")).toBe(true);
    expect(containsForbiddenExportPhrase("胜诉概率约 80%")).toBe(true);
    expect(containsForbiddenExportPhrase("保证无遗漏")).toBe(true);
    expect(containsForbiddenExportPhrase("北京市第一中级人民法院")).toBe(false);
  });
});

describe("Markdown / CSV 生成只含白名单且带免责说明", () => {
  it("Markdown 含标题、免责区块、表头，且无正文 / 无禁用话术", () => {
    const md = generateMarkdown(makeList(), [...EXPORT_FIELD_WHITELIST]);
    // 含全部免责行。
    for (const line of EXPORT_DISCLAIMER_LINES) {
      expect(md).toContain(line);
    }
    // 含元数据。
    expect(md).toContain("（2021）京01民终123号");
    expect(md).toContain("北京市第一中级人民法院");
    expect(md).toContain("case-1#chunk-9");
    expect(md).toContain("重点关注违约金认定");
    // 不含正文标志。
    for (const marker of BODY_MARKERS) {
      expect(md).not.toContain(marker);
    }
    // 不含禁用话术。
    expect(containsForbiddenExportPhrase(md)).toBe(false);
  });

  it("CSV 首部为免责注释行、随后表头 + 数据行，且无正文 / 无禁用话术", () => {
    const csv = generateCsv(makeList(), [...EXPORT_FIELD_WHITELIST]);
    const lines = csv.split("\n");
    // 免责注释行以 # 开头。
    expect(lines[0].startsWith("# ")).toBe(true);
    for (const line of EXPORT_DISCLAIMER_LINES) {
      expect(csv).toContain(`# ${line}`);
    }
    // 表头含中文列名。
    expect(csv).toContain(EXPORT_FIELD_LABELS.case_number);
    expect(csv).toContain("案号");
    for (const marker of BODY_MARKERS) {
      expect(csv).not.toContain(marker);
    }
    expect(containsForbiddenExportPhrase(csv)).toBe(false);
  });

  it("CSV / Markdown 转义防止注入与结构破坏", () => {
    expect(escapeCsvCell('a,b')).toBe('"a,b"');
    expect(escapeCsvCell('say "hi"')).toBe('"say ""hi"""');
    expect(escapeCsvCell("line1\nline2")).toBe("line1 line2");
    expect(escapeMarkdownCell("a|b")).toBe("a\\|b");
    expect(escapeMarkdownCell("x\ny")).toBe("x y");
  });

  it("即使用户备注里塞入正文标志，也只是被当作短字段值（不扩散为列）", () => {
    // 备注是白名单短字段，用户可写任何内容；导出器不额外引入正文列。
    const list = makeList({
      items: [makeItem({ note: "本院认为应支持" })],
    });
    const md = generateMarkdown(list, [...EXPORT_FIELD_WHITELIST]);
    // 该字符串作为用户备注出现一次，但不应引入「裁判文书正文」列或额外正文。
    // 关键断言：导出列集合不变，仍是白名单。
    expect(md).toContain("本院认为应支持");
    // 列数 = 白名单列数（表头行竖线数固定）。
    const headerLine = md.split("\n").find((l) => l.includes("案号") && l.includes("|"))!;
    const colCount = headerLine.split("|").filter((s) => s.trim()).length;
    expect(colCount).toBe(EXPORT_FIELD_WHITELIST.length);
  });
});

describe("exportCaseList 主入口与安全降级", () => {
  it("正常导出：触发下载、状态 exported、descriptor 仅含引用 / 配置 / 计数", () => {
    const dl = memoryDownloader();
    const result = exportCaseList(makeList(), { format: "markdown", downloader: dl.fn });
    expect(result.descriptor.export_status).toBe("exported");
    expect(result.descriptor.degrade_reason).toBeNull();
    expect(result.descriptor.list_id).toBe("list-1");
    expect(result.descriptor.item_count).toBe(1);
    expect(dl.calls).toHaveLength(1);
    expect(dl.calls[0].filename.endsWith(".md")).toBe(true);
    // descriptor 不得含正文字段。
    const keys = Object.keys(result.descriptor);
    expect(keys).not.toContain("items");
    expect(keys).not.toContain("content");
    expect(JSON.stringify(result.descriptor)).not.toContain("本院认为");
  });

  it("CSV 导出 mime / 扩展名正确", () => {
    const dl = memoryDownloader();
    const result = exportCaseList(makeList(), { format: "csv", downloader: dl.fn });
    expect(result.descriptor.export_format).toBe("csv");
    expect(dl.calls[0].mimeType).toContain("text/csv");
    expect(dl.calls[0].filename.endsWith(".csv")).toBe(true);
  });

  it("空清单 → empty，不触发下载", () => {
    const dl = memoryDownloader();
    const result = exportCaseList(makeList({ items: [] }), { downloader: dl.fn });
    expect(result.descriptor.export_status).toBe("empty");
    expect(result.descriptor.degrade_reason).toBe("empty_list");
    expect(dl.calls).toHaveLength(0);
    expect(result.content).toBeNull();
  });

  it("缺失清单 → failed / list_not_found", () => {
    const result = exportCaseList(null, { downloader: memoryDownloader().fn });
    expect(result.descriptor.export_status).toBe("failed");
    expect(result.descriptor.degrade_reason).toBe("list_not_found");
  });

  it("下载器不可用（null）→ degraded / download_unavailable，但内容已生成可复核", () => {
    const result = exportCaseList(makeList(), { downloader: null });
    expect(result.descriptor.export_status).toBe("degraded");
    expect(result.descriptor.degrade_reason).toBe("download_unavailable");
    expect(result.content).toBeTruthy();
  });

  it("下载器抛错 → degraded / download_failed，不抛出异常", () => {
    const thrower: DownloaderLike = () => {
      throw new Error("blocked");
    };
    expect(() =>
      exportCaseList(makeList(), { downloader: thrower })
    ).not.toThrow();
    const result = exportCaseList(makeList(), { downloader: thrower });
    expect(result.descriptor.export_status).toBe("degraded");
    expect(result.descriptor.degrade_reason).toBe("download_failed");
  });

  it("非法 format 回退 markdown", () => {
    const dl = memoryDownloader();
    const result = exportCaseList(makeList(), {
      format: "pdf" as unknown as "markdown",
      downloader: dl.fn,
    });
    expect(result.descriptor.export_format).toBe("markdown");
  });
});

describe("脱敏日志", () => {
  it("buildCaseListExportLog 只含 event / format / status / reason_code / count", () => {
    const dl = memoryDownloader();
    const result = exportCaseList(makeList(), { format: "csv", downloader: dl.fn });
    const log = buildCaseListExportLog(result.descriptor);
    expect(Object.keys(log).sort()).toEqual(
      ["count", "event", "format", "reason_code", "status"].sort()
    );
    expect(log.event).toBe("case_list_export");
    expect(log.format).toBe("csv");
    expect(log.status).toBe("exported");
    // 日志不含案号 / note / tag / title / 正文。
    const serialized = JSON.stringify(log);
    expect(serialized).not.toContain("（2021）京01民终123号");
    expect(serialized).not.toContain("违约金");
    expect(serialized).not.toContain("买卖合同类案");
    expect(serialized).not.toContain("本院认为");
  });

  it("logCaseListExport 用注入 logger，且 logger 抛错不影响主链路", () => {
    const dl = memoryDownloader();
    const result = exportCaseList(makeList(), { downloader: dl.fn });
    const spy = vi.fn();
    logCaseListExport(result.descriptor, spy);
    expect(spy).toHaveBeenCalledOnce();
    expect(() =>
      logCaseListExport(result.descriptor, () => {
        throw new Error("logger blew up");
      })
    ).not.toThrow();
  });

  it("reason code 常量集合稳定", () => {
    expect([...CASE_LIST_EXPORT_REASON_CODES].sort()).toEqual(
      [
        "download_failed",
        "download_unavailable",
        "empty_list",
        "list_not_found",
        "no_fields",
        "unsupported_format",
      ].sort()
    );
  });
});

describe("文件名安全化", () => {
  it("buildExportFilename 去除路径分隔符与特殊字符、带时间戳与扩展名", () => {
    const name = buildExportFilename(
      makeList({ list_title: "a/b:c*?<>|d 我的清单" }),
      "csv"
    );
    expect(name).not.toMatch(/[\\/:*?"<>|]/);
    expect(name.endsWith(".csv")).toBe(true);
  });

  it("generateExportContent format 路由正确", () => {
    const md = generateExportContent(makeList(), "markdown", [...EXPORT_FIELD_WHITELIST]);
    const csv = generateExportContent(makeList(), "csv", [...EXPORT_FIELD_WHITELIST]);
    expect(md.startsWith("#")).toBe(true);
    expect(csv.split("\n")[0].startsWith("# ")).toBe(true);
  });
});
