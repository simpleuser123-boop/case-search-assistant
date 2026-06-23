import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import { BulkImportPanel } from "./BulkImportPanel";
import { clearSession, setSession } from "../../lib/sessionState";

vi.mock("../../config/featureFlags", () => ({
  isBulkImportEnabled: vi.fn(() => false),
}));
vi.mock("../../services/bulkImportApi", () => ({
  runBulkImport: vi.fn(async () => ({
    ok: true,
    data: {
      ok: true,
      import_job_id: "imp_1",
      import_status: "partial",
      item_count: 2,
      imported_count: 1,
      rejected_count: 1,
      duplicate_count: 0,
      degrade_reason: "partial_import",
      outcomes: [
        { case_id: "c_1", ok: true, reason_code: "ok", object_id: "o_1" },
        { case_id: "c_2", ok: false, reason_code: "missing_source_anchor", object_id: null },
      ],
    },
  })),
}));

import { isBulkImportEnabled } from "../../config/featureFlags";
import { runBulkImport } from "../../services/bulkImportApi";

const flagMock = vi.mocked(isBulkImportEnabled);
const runMock = vi.mocked(runBulkImport);

const SESSION = {
  account: { user_id: "u_1", display_name: "A", account_status: "active", auth_provider: "local" as const },
  sessionToken: "tok",
  expiresAt: null,
};

beforeEach(() => {
  flagMock.mockReturnValue(false);
  runMock.mockClear();
  clearSession();
});

afterEach(() => {
  cleanup();
  clearSession();
});

describe("BulkImportPanel (flag-gated)", () => {
  it("renders nothing when bulk import is disabled (M5-5/M4 end state)", () => {
    flagMock.mockReturnValue(false);
    setSession(SESSION);
    const { container } = render(<BulkImportPanel />);
    expect(container.firstChild).toBeNull();
    expect(screen.queryByLabelText("批量导入")).toBeNull();
  });

  it("renders read-only entry when enabled but not logged in", () => {
    flagMock.mockReturnValue(true);
    clearSession();
    render(<BulkImportPanel />);
    expect(screen.getByLabelText("批量导入")).toBeInTheDocument();
    expect(screen.getByText(/请先登录账号/)).toBeInTheDocument();
    expect(screen.queryByRole("textbox")).toBeNull();
    expect(runMock).not.toHaveBeenCalled();
  });

  it("renders import controls and only-metadata hint when enabled and logged in", () => {
    flagMock.mockReturnValue(true);
    setSession(SESSION);
    render(<BulkImportPanel />);
    expect(screen.getByLabelText("批量导入")).toBeInTheDocument();
    expect(screen.getByText(/只导入元数据与引用，绝不导入正文/)).toBeInTheDocument();
    expect(screen.getByText("开始导入")).toBeInTheDocument();
  });

  it("parses pasted lines into whitelisted items only — body columns never sent", async () => {
    flagMock.mockReturnValue(true);
    setSession(SESSION);
    render(<BulkImportPanel />);
    const textarea = screen.getByRole("textbox");
    // 第 5 列是多余的「正文」列，解析器应忽略它，绝不进入请求体。
    fireEvent.change(textarea, {
      target: { value: "c_1, (2021)京01民终123号, 北京一中院, chunk_7, 这是案情正文应被忽略" },
    });
    fireEvent.click(screen.getByText("开始导入"));
    await waitFor(() => expect(runMock).toHaveBeenCalledTimes(1));
    const arg = runMock.mock.calls[0][0];
    const serialized = JSON.stringify(arg);
    expect(serialized).not.toContain("案情正文");
    // 锚点由 case_id + source_chunk_id 组成。
    expect(arg.items[0].sourceAnchors?.[0]).toEqual({ case_id: "c_1", source_chunk_id: "chunk_7" });
    // 结果明细里展示降级 reason。
    await waitFor(() => expect(screen.getByText(/missing_source_anchor/)).toBeInTheDocument());
  });
});
