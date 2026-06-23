import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import { PermissionPanel } from "./PermissionPanel";
import { clearSession, setSession } from "../../lib/sessionState";

vi.mock("../../config/featureFlags", () => ({
  isPermissionTieringEnabled: vi.fn(() => false),
}));
vi.mock("../../services/permissionApi", () => ({
  grant: vi.fn(async () => ({ ok: true, data: { reason_code: "ok" } })),
  revoke: vi.fn(async () => ({ ok: true, data: { reason_code: "ok" } })),
  readObject: vi.fn(async () => ({ ok: true, data: { effective_level: "owner", object: null } })),
}));

import { isPermissionTieringEnabled } from "../../config/featureFlags";
import { grant, readObject, revoke } from "../../services/permissionApi";

const flagMock = vi.mocked(isPermissionTieringEnabled);
const grantMock = vi.mocked(grant);
const revokeMock = vi.mocked(revoke);
const readObjectMock = vi.mocked(readObject);

beforeEach(() => {
  flagMock.mockReturnValue(false);
  grantMock.mockClear();
  revokeMock.mockClear();
  readObjectMock.mockClear();
  clearSession();
});

afterEach(() => {
  cleanup();
  clearSession();
});

describe("PermissionPanel (flag-gated)", () => {
  it("renders nothing when permission tiering is disabled (M5-3/M4 end state)", () => {
    flagMock.mockReturnValue(false);
    setSession({
      account: { user_id: "u_1", display_name: "A", account_status: "active", auth_provider: "local" },
      sessionToken: "tok",
      expiresAt: null,
    });
    const { container } = render(<PermissionPanel />);
    expect(container.firstChild).toBeNull();
    expect(screen.queryByLabelText("权限分级")).toBeNull();
  });

  it("renders read-only entry when enabled but not logged in", () => {
    flagMock.mockReturnValue(true);
    clearSession();
    render(<PermissionPanel />);
    expect(screen.getByLabelText("权限分级")).toBeInTheDocument();
    expect(screen.getByText(/请先登录账号/)).toBeInTheDocument();
    expect(screen.queryByPlaceholderText("对象 ID（收藏/清单/报告）")).toBeNull();
    expect(grantMock).not.toHaveBeenCalled();
    expect(revokeMock).not.toHaveBeenCalled();
    expect(readObjectMock).not.toHaveBeenCalled();
  });

  it("renders authorization controls when enabled and logged in", () => {
    flagMock.mockReturnValue(true);
    setSession({
      account: { user_id: "u_1", display_name: "A", account_status: "active", auth_provider: "local" },
      sessionToken: "tok",
      expiresAt: null,
    });
    render(<PermissionPanel />);
    expect(screen.getByLabelText("权限分级")).toBeInTheDocument();
    // 默认最小权限说明文案存在
    expect(screen.getByText(/默认最小权限/)).toBeInTheDocument();
    expect(screen.getByLabelText("权限等级")).toBeInTheDocument();
  });
});
