import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";

import { TendencyAnalysisPanel } from "./TendencyAnalysisPanel";

vi.mock("../../config/featureFlags", () => ({
  isTendencyAnalysisEnabled: vi.fn(() => false),
}));
vi.mock("../../services/tendencyApi", () => ({
  fetchTendencyAnalysis: vi.fn(),
}));

import { isTendencyAnalysisEnabled } from "../../config/featureFlags";
import { fetchTendencyAnalysis } from "../../services/tendencyApi";

const flagMock = vi.mocked(isTendencyAnalysisEnabled);
const fetchMock = vi.mocked(fetchTendencyAnalysis);

const SAMPLE_ANALYSIS = {
  version: "m5-8-tendency-analysis-v1",
  enabled: true,
  gate_passed: true,
  data_source: "data/processed/cases.jsonl（JuDGE 刑事判决，只读元数据统计）",
  coverage_range: "覆盖范围：只读元数据；纳入聚合样本 80996 条；维度=法院层级/审级/案件领域/案由。",
  total_sample_size: 80996,
  min_sample_threshold: 30,
  disclaimer:
    "本分析为基于现有数据覆盖的聚合统计参考，可能未覆盖全部案例；不构成法律意见，不预测个案结果，需人工复核。",
  aggregations: [
    {
      dimension: "court_level",
      name: "法院层级分布",
      sample_size: 80913,
      coverage_range: "覆盖范围：只读元数据。",
      data_source: "data/processed/cases.jsonl",
      confidence_note: "该维度纳入样本 80913 条；占比为历史数据的统计分布，不代表未来个案结果。",
      insufficient_dimension: false,
      buckets: [
        { label: "基层", sample_size: 68018, share: 0.8406, sample_sufficient: true, case_id_refs: ["ws_a", "ws_b"], case_id_total: 68018 },
        { label: "中级", sample_size: 11898, share: 0.147, sample_sufficient: true, case_id_refs: ["ws_c"], case_id_total: 11898 },
        { label: "稀有层级", sample_size: 5, share: 0.0001, sample_sufficient: false, case_id_refs: [], case_id_total: 5 },
      ],
    },
  ],
};

beforeEach(() => {
  flagMock.mockReturnValue(false);
  fetchMock.mockReset();
});

afterEach(() => {
  cleanup();
});

describe("TendencyAnalysisPanel (flag-gated, F19)", () => {
  it("renders nothing when tendency analysis is disabled (M5-7 end state)", () => {
    flagMock.mockReturnValue(false);
    const { container } = render(<TendencyAnalysisPanel />);
    expect(container.firstChild).toBeNull();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("shows unavailable message when backend gate/flag blocks (403), no aggregations", async () => {
    flagMock.mockReturnValue(true);
    fetchMock.mockResolvedValue({ ok: false, reason: "unavailable", status: 403, reasonCode: "TENDENCY_ANALYSIS_UNAVAILABLE" });
    render(<TendencyAnalysisPanel />);
    await waitFor(() => expect(screen.getByText(/暂不可用/)).toBeInTheDocument());
    expect(screen.queryByText("法院层级分布")).toBeNull();
  });

  it("renders aggregations with sample size, coverage, case_id refs and disclaimer", async () => {
    flagMock.mockReturnValue(true);
    fetchMock.mockResolvedValue({ ok: true, data: SAMPLE_ANALYSIS });
    render(<TendencyAnalysisPanel />);
    await waitFor(() => expect(screen.getByText("法院层级分布")).toBeInTheDocument());
    // 样本量与覆盖范围标注
    expect(screen.getByText(/纳入聚合总样本 80996 条/)).toBeInTheDocument();
    expect(screen.getByText(/数据来源：/)).toBeInTheDocument();
    // 可追溯到来源 case_id
    expect(screen.getByText(/ws_a/)).toBeInTheDocument();
    // 充足分组解读占比，不足分组明确标注且不解读
    expect(screen.getByText(/占比 84.1%/)).toBeInTheDocument();
    expect(screen.getByText(/样本不足，不解读占比/)).toBeInTheDocument();
    // 强制免责说明
    expect(screen.getByText(/不构成法律意见，不预测个案结果，需人工复核/)).toBeInTheDocument();
  });

  it("never renders individual-case prediction / win-loss probability / definitive conclusion text", async () => {
    flagMock.mockReturnValue(true);
    fetchMock.mockResolvedValue({ ok: true, data: SAMPLE_ANALYSIS });
    const { container } = render(<TendencyAnalysisPanel />);
    await waitFor(() => expect(screen.getByText("法院层级分布")).toBeInTheDocument());
    const text = container.textContent ?? "";
    for (const banned of ["胜诉率", "败诉率", "胜诉概率", "败诉概率", "胜算", "判决预测", "必然胜诉", "该法官会判"]) {
      expect(text).not.toContain(banned);
    }
  });
});
