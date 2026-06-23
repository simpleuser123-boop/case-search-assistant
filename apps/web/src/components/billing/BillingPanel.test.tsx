import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import { BillingPanel } from "./BillingPanel";

vi.mock("../../config/featureFlags", () => ({
  isBillingEnabled: vi.fn(() => false),
}));
vi.mock("../../services/billingApi", () => ({
  fetchPlans: vi.fn(),
  fetchMySubscription: vi.fn(),
  startTrial: vi.fn(),
  submitRenewalIntent: vi.fn(),
}));

import { isBillingEnabled } from "../../config/featureFlags";
import {
  fetchMySubscription,
  fetchPlans,
  startTrial,
  submitRenewalIntent,
} from "../../services/billingApi";

const flagMock = vi.mocked(isBillingEnabled);
const plansMock = vi.mocked(fetchPlans);
const subMock = vi.mocked(fetchMySubscription);
const trialMock = vi.mocked(startTrial);
const intentMock = vi.mocked(submitRenewalIntent);

const SAMPLE_PLANS = {
  ok: true,
  items: [
    {
      billing_plan_id: "plan_team_pro",
      plan_name: "团队专业版",
      quota_label: "5 席位 / 月 2000 次检索 / 团队共享",
      price_display: "¥1980/年",
      billing_cycle: "yearly",
      seat_quota: 5,
      trial_days: 14,
      entitled_features: ["ENABLE_TEAM_WORKSPACE"],
      sort_order: 1,
    },
  ],
};

const SAMPLE_SUB = {
  subscription_id: "sub_abc",
  billing_plan_id: "plan_team_pro",
  trial_status: "active",
  subscription_status: "trialing",
  renewal_intent: "unknown",
  renewal_reason: null,
  trial_ends_at: "2026-07-01T00:00:00+00:00",
  current_period_end: "2026-07-01T00:00:00+00:00",
  owner_user_id_hash: "uidh_abc123",
  team_id_hash: "tidh_none",
};

beforeEach(() => {
  flagMock.mockReturnValue(false);
  plansMock.mockReset();
  subMock.mockReset();
  trialMock.mockReset();
  intentMock.mockReset();
  subMock.mockResolvedValue({ ok: true, data: { ok: true, subscription: null } });
});

afterEach(() => {
  cleanup();
});

describe("BillingPanel (flag-gated, M5-9)", () => {
  it("renders nothing when billing is disabled (M5-8 end state)", () => {
    flagMock.mockReturnValue(false);
    const { container } = render(<BillingPanel />);
    expect(container.firstChild).toBeNull();
    expect(plansMock).not.toHaveBeenCalled();
  });

  it("shows disabled message when backend returns 403", async () => {
    flagMock.mockReturnValue(true);
    plansMock.mockResolvedValue({ ok: false, reason: "disabled", status: 403, reasonCode: "BILLING_DISABLED" });
    render(<BillingPanel />);
    await waitFor(() => expect(screen.getByText(/暂未启用/)).toBeInTheDocument());
    expect(screen.queryByText("团队专业版")).toBeNull();
  });

  it("renders plan catalog with price/quota and a trial button", async () => {
    flagMock.mockReturnValue(true);
    plansMock.mockResolvedValue({ ok: true, data: SAMPLE_PLANS });
    render(<BillingPanel />);
    await waitFor(() => expect(screen.getByText("团队专业版")).toBeInTheDocument());
    expect(screen.getByText("¥1980/年")).toBeInTheDocument();
    expect(screen.getByText(/开通 14 天试用/)).toBeInTheDocument();
  });

  it("never renders any payment-credential input field", async () => {
    flagMock.mockReturnValue(true);
    plansMock.mockResolvedValue({ ok: true, data: SAMPLE_PLANS });
    subMock.mockResolvedValue({ ok: true, data: { ok: true, subscription: SAMPLE_SUB } });
    const { container } = render(<BillingPanel />);
    await waitFor(() => expect(screen.getByText("团队专业版")).toBeInTheDocument());
    // 不得出现任何凭据型输入控件：扫描所有 input/textarea 的 name/id/type/placeholder。
    const fields = Array.from(container.querySelectorAll("input, textarea"));
    const CRED = ["card", "cardnumber", "card_number", "cvv", "cvc", "bank", "account", "iban", "token", "卡号", "银行", "支付密码"];
    for (const el of fields) {
      // 不得有密码型输入框（凭据采集的典型形态）
      expect((el.getAttribute("type") ?? "").toLowerCase()).not.toBe("password");
      const meta = [
        el.getAttribute("name"),
        el.getAttribute("id"),
        el.getAttribute("placeholder"),
        el.getAttribute("aria-label"),
      ].join(" ").toLowerCase();
      for (const c of CRED) {
        expect(meta).not.toContain(c.toLowerCase());
      }
    }
    // 仅有的输入控件是续费意愿单选 + 自填说明 textarea（非凭据）
    expect(fields.length).toBeGreaterThan(0);
    // 显式提示不要输入支付信息
    expect(screen.getByText(/不收集任何卡号/)).toBeInTheDocument();
  });

  it("collects renewal intent (no credential fields in the request)", async () => {
    flagMock.mockReturnValue(true);
    plansMock.mockResolvedValue({ ok: true, data: SAMPLE_PLANS });
    subMock.mockResolvedValue({ ok: true, data: { ok: true, subscription: SAMPLE_SUB } });
    intentMock.mockResolvedValue({
      ok: true,
      data: { ok: true, subscription: { ...SAMPLE_SUB, renewal_intent: "will_renew" } },
    });
    render(<BillingPanel sessionToken="tok" />);
    await waitFor(() => expect(screen.getByText("续费意愿")).toBeInTheDocument());
    fireEvent.click(screen.getByText("提交续费意愿"));
    await waitFor(() => expect(intentMock).toHaveBeenCalled());
    const args = intentMock.mock.calls[0];
    // 参数：subscriptionId, renewalIntent, renewalReason, token —— 无任何凭据
    expect(args[0]).toBe("sub_abc");
    expect(["will_renew", "undecided", "will_churn"]).toContain(args[1]);
    await waitFor(() => expect(screen.getByText(/续费意愿已记录/)).toBeInTheDocument());
  });
});
