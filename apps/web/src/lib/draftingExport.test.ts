// E6-4 文书工作台导出测试（draftingExport）。
//
// 守门重点（对标 M4-5 导出边界 + E6 红线）：
//   - 导出文件头部强制含数据覆盖说明 + 免责声明（断言关键文案存在，不可关闭）。
//   - 导出内容只含元数据 + 来源锚点 + 用户备注；正文型 / 胜负 / 结论文案 0 命中（扫描断言）。
//   - structure_skeleton 导出为标题清单，无段落正文。
//   - 无锚点引用不进入导出。
//   - article_text（即便后端回填语料条文）不进入导出。
//   - 导出失败 / 环境不支持时安全降级为状态 + reason_code，绝不抛出。
//   - 脱敏日志只含 format / status / reason_code / 计数，不含正文 / 标题 / 案号 / note / tag。

import { describe, it, expect, vi } from "vitest";

import {
  exportDraft,
  generateDraftExportContent,
  buildDraftExportLog,
  logDraftExport,
  containsForbiddenExportPhrase,
  collectCandidateRows,
  collectStatuteRows,
  DRAFT_EXPORT_DISCLAIMER_LINES,
  type DownloaderLike,
} from "./draftingExport";
import type { DraftDescriptorView } from "../services/draftingApi";

// 一份含「带锚点 + 无锚点」引用、note/tag 的草稿样本。
function sampleDraft(overrides: Partial<DraftDescriptorView> = {}): DraftDescriptorView {
  return {
    draft_id: "draft_001",
    structure_skeleton: ["一、事实与争议焦点", "二、法律适用", "三、结论"],
    candidate_refs: [
      {
        case_id: "case_a",
        case_number: "(2021)京01刑初123号",
        court: "北京市第一中级人民法院",
        trial_level: "一审",
        case_cause: "盗窃罪",
        judgment_date: "2021-06-01",
        source_anchors: [{ case_id: "case_a", source_chunk_id: "chunk_3" }],
      },
      // 无锚点：必须被丢弃。
      {
        case_id: "case_b",
        case_number: "(2022)沪02刑终99号",
        court: "上海市第二中级人民法院",
        source_anchors: [],
      },
    ],
    statute_refs: [
      {
        statute_id: "stat_264",
        law_name: "中华人民共和国刑法",
        article_no: "第二百六十四条",
        statute_anchors: [{ text_id: "law#264" }],
        // article_text 即便被回填，导出也不得携带条文正文。
        article_text: "【虚构条文正文，导出绝不应出现】盗窃公私财物，数额较大的……",
      },
      // 无锚点：必须被丢弃。
      {
        statute_id: "stat_999",
        law_name: "某法",
        statute_anchors: [],
      },
    ],
    note: "重点核对二审改判理由",
    tag: "盗窃-二审",
    owner_user_id: "user_1",
    team_id: null,
    visibility: "private",
    status: "active",
    created_at: "2026-06-18T00:00:00Z",
    updated_at: "2026-06-18T00:00:00Z",
    ...overrides,
  };
}

// 正文型 / 胜负 / 结论关键词（命中即视为越线）。
const BODY_LIKE_TOKENS = [
  "虚构条文正文",
  "盗窃公私财物，数额较大",
  "judgment_text",
  "chunk_text",
  "本院认为",
  "胜诉",
  "败诉",
  "胜率",
  "必然",
];

// 剔除免责头行（胜诉/败诉等词只允许出现在免责头的否定式表述中）。
function stripDisclaimer(content: string): string {
  return content
    .split("\n")
    .filter((line) => !DRAFT_EXPORT_DISCLAIMER_LINES.some((d) => line.includes(d)))
    .join("\n");
}

describe("draftingExport - 免责头强制注入", () => {
  it("Markdown 导出头部含全部免责 / 数据覆盖文案", () => {
    const content = generateDraftExportContent(sampleDraft(), "markdown");
    for (const line of DRAFT_EXPORT_DISCLAIMER_LINES) {
      expect(content).toContain(line);
    }
    // 关键口径词
    expect(content).toContain("数据覆盖说明");
    expect(content).toContain("不构成法律意见");
    expect(content).toContain("人工复核");
    expect(content).toContain("裁判结果预测");
  });

  it("纯文本导出头部同样含免责 / 数据覆盖文案", () => {
    const content = generateDraftExportContent(sampleDraft(), "text");
    for (const line of DRAFT_EXPORT_DISCLAIMER_LINES) {
      expect(content).toContain(line);
    }
  });

  it("免责文案自身不含禁用绝对话术 / 胜负判断", () => {
    for (const line of DRAFT_EXPORT_DISCLAIMER_LINES) {
      expect(containsForbiddenExportPhrase(line)).toBe(false);
    }
  });
});

describe("draftingExport - 导出边界（零正文 / 零结论）", () => {
  it("Markdown 导出含元数据 + 锚点 + 备注，正文型内容 0 命中", () => {
    const content = generateDraftExportContent(sampleDraft(), "markdown");
    // 元数据存在
    expect(content).toContain("(2021)京01刑初123号");
    expect(content).toContain("北京市第一中级人民法院");
    expect(content).toContain("盗窃罪");
    expect(content).toContain("第二百六十四条");
    // 锚点存在
    expect(content).toContain("case_a#chunk_3");
    expect(content).toContain("law#264");
    // 用户备注存在
    expect(content).toContain("重点核对二审改判理由");
    expect(content).toContain("盗窃-二审");
    // 数据区（剔除免责头）正文 / 胜负 / 结论 0 命中。
    // 注：胜诉 / 败诉等词只允许出现在免责头的否定式表述中，故扫描前剔除免责行。
    const dataSection = stripDisclaimer(content);
    for (const token of BODY_LIKE_TOKENS) {
      expect(dataSection.includes(token)).toBe(false);
    }
    expect(containsForbiddenExportPhrase(dataSection)).toBe(false);
  });

  it("article_text 不进入导出（条文正文零承载）", () => {
    const content = generateDraftExportContent(sampleDraft(), "markdown");
    expect(content).not.toContain("虚构条文正文");
    expect(content).not.toContain("盗窃公私财物，数额较大");
  });

  it("structure_skeleton 导出为标题清单（无段落正文）", () => {
    const content = generateDraftExportContent(sampleDraft(), "markdown");
    expect(content).toContain("一、事实与争议焦点");
    expect(content).toContain("二、法律适用");
    expect(content).toContain("三、结论");
  });
});

describe("draftingExport - 无锚点引用丢弃", () => {
  it("无锚点类案 / 法条引用不进入导出", () => {
    const content = generateDraftExportContent(sampleDraft(), "markdown");
    // 无锚点 case_b / 某法 不出现
    expect(content).not.toContain("(2022)沪02刑终99号");
    expect(content).not.toContain("上海市第二中级人民法院");
    expect(content).not.toContain("某法");
  });

  it("collect* 只返回带锚点引用", () => {
    const draft = sampleDraft();
    expect(collectCandidateRows(draft)).toHaveLength(1);
    expect(collectStatuteRows(draft)).toHaveLength(1);
    expect(collectCandidateRows(draft)[0].case_id).toBe("case_a");
    expect(collectStatuteRows(draft)[0].statute_id).toBe("stat_264");
  });
});

describe("draftingExport - 主入口 / 安全降级", () => {
  it("成功导出触发下载并返回 exported", () => {
    const captured: { filename: string; content: string; mimeType: string }[] = [];
    const downloader: DownloaderLike = (file) => captured.push(file);
    const result = exportDraft(sampleDraft(), { format: "markdown", downloader });
    expect(result.descriptor.export_status).toBe("exported");
    expect(result.descriptor.degrade_reason).toBeNull();
    expect(result.descriptor.candidate_count).toBe(1);
    expect(result.descriptor.statute_count).toBe(1);
    expect(captured).toHaveLength(1);
    expect(captured[0].filename).toMatch(/\.md$/);
    expect(captured[0].mimeType).toContain("markdown");
  });

  it("draft 为 null -> failed/draft_not_found，不抛出", () => {
    const result = exportDraft(null, { downloader: () => {} });
    expect(result.descriptor.export_status).toBe("failed");
    expect(result.descriptor.degrade_reason).toBe("draft_not_found");
    expect(result.content).toBeNull();
  });

  it("空骨架 -> empty/empty_skeleton，不抛出", () => {
    const result = exportDraft(sampleDraft({ structure_skeleton: [] }), {
      downloader: () => {},
    });
    expect(result.descriptor.export_status).toBe("empty");
    expect(result.descriptor.degrade_reason).toBe("empty_skeleton");
  });

  it("下载器抛错 -> degraded/download_failed，内容仍返回，不抛出", () => {
    const downloader: DownloaderLike = () => {
      throw new Error("boom");
    };
    const result = exportDraft(sampleDraft(), { format: "markdown", downloader });
    expect(result.descriptor.export_status).toBe("degraded");
    expect(result.descriptor.degrade_reason).toBe("download_failed");
    expect(result.content).not.toBeNull();
  });

  it("无下载器 -> degraded/download_unavailable", () => {
    const result = exportDraft(sampleDraft(), { format: "markdown", downloader: null });
    expect(result.descriptor.export_status).toBe("degraded");
    expect(result.descriptor.degrade_reason).toBe("download_unavailable");
    expect(result.content).not.toBeNull();
  });
});

describe("draftingExport - 脱敏日志", () => {
  it("日志只含 format/status/reason_code/计数，不含正文 / 标题 / 案号 / note / tag", () => {
    const result = exportDraft(sampleDraft(), { format: "markdown", downloader: () => {} });
    const log = buildDraftExportLog(result.descriptor);
    expect(log).toEqual({
      event: "drafting_export",
      format: "markdown",
      status: "exported",
      reason_code: null,
      skeleton_count: 3,
      candidate_count: 1,
      statute_count: 1,
    });
    const serialized = JSON.stringify(log);
    expect(serialized).not.toContain("(2021)京01刑初123号");
    expect(serialized).not.toContain("重点核对");
    expect(serialized).not.toContain("盗窃-二审");
    expect(serialized).not.toContain("一、事实与争议焦点");
    expect(serialized).not.toContain("draft_001");
  });

  it("logDraftExport 注入自定义 logger，logger 抛错也不影响主链路", () => {
    const spy = vi.fn(() => {
      throw new Error("logger boom");
    });
    const result = exportDraft(sampleDraft(), { downloader: () => {} });
    // logDraftExport 内部吞掉 logger 异常，不应抛出
    expect(() => logDraftExport(result.descriptor, spy)).not.toThrow();
    expect(spy).toHaveBeenCalledTimes(1);
  });
});
