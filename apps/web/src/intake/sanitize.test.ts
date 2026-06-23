import { describe, expect, it } from "vitest";

import {
  buildSearchProfileFromRaw,
  extractCaseCause,
  extractDisputeFocusKeywords,
  extractRegion,
  extractTrialLevelPreference,
  PLACEHOLDER_EMAIL,
  PLACEHOLDER_ID_CARD,
  PLACEHOLDER_NAME,
  PLACEHOLDER_PHONE,
  redactPII,
  SEARCH_PROFILE_FIELDS,
} from "./sanitize";

// --- 短假案情 fixture（与后端 test_e4_intake_sanitize.py 共享口径，纯假数据）---
const FAKE_RAW_CASE =
  "原告张三与被告李四买卖合同纠纷一案，在上海某区审理。" +
  "张三手机号13800001111，身份证110101199001011234，" +
  "邮箱zhangsan@example.com。双方就货款与违约金存在争议，已进入二审。";

const PII_TOKENS = [
  "张三",
  "李四",
  "13800001111",
  "110101199001011234",
  "zhangsan@example.com",
];

describe("redactPII (移除/占位，0 残留)", () => {
  it("removes all PII tokens and inserts placeholders", () => {
    const redacted = redactPII(FAKE_RAW_CASE);
    for (const token of PII_TOKENS) {
      expect(redacted).not.toContain(token);
    }
    expect(redacted).toContain(PLACEHOLDER_PHONE);
    expect(redacted).toContain(PLACEHOLDER_ID_CARD);
    expect(redacted).toContain(PLACEHOLDER_EMAIL);
    expect(redacted).toContain(PLACEHOLDER_NAME);
  });

  it("is pure on empty input", () => {
    expect(redactPII("")).toBe("");
  });

  it("redacts standalone name repetition", () => {
    const redacted = redactPII("原告张三主张权利，张三另行举证。");
    expect(redacted).not.toContain("张三");
  });

  it("keeps role label, redacts name", () => {
    const redacted = redactPII("被告李四未到庭");
    expect(redacted).toContain("被告");
    expect(redacted).not.toContain("李四");
  });
});

describe("buildSearchProfileFromRaw (白名单 + 0 PII)", () => {
  it("output keys are strictly the whitelist five fields", () => {
    const profile = buildSearchProfileFromRaw(FAKE_RAW_CASE);
    expect(new Set(Object.keys(profile))).toEqual(new Set(SEARCH_PROFILE_FIELDS));
  });

  it("has zero PII residue across all fields", () => {
    const profile = buildSearchProfileFromRaw(FAKE_RAW_CASE);
    const blob = JSON.stringify(profile);
    for (const token of PII_TOKENS) {
      expect(blob).not.toContain(token);
    }
  });

  it("query_text is present and redacted", () => {
    const profile = buildSearchProfileFromRaw(FAKE_RAW_CASE);
    expect(profile.query_text.length).toBeGreaterThan(0);
    for (const token of PII_TOKENS) {
      expect(profile.query_text).not.toContain(token);
    }
  });

  it("caps query_text length", () => {
    const longText = "买卖合同纠纷。" + "争议".repeat(500);
    const profile = buildSearchProfileFromRaw(longText);
    expect(profile.query_text.length).toBeLessThanOrEqual(280);
  });
});

describe("结构化抽取口径 (与后端一致)", () => {
  it("extracts known case cause", () => {
    expect(extractCaseCause("这是一起买卖合同纠纷")).toBe("买卖合同纠纷");
  });

  it("falls back to charge name", () => {
    expect(extractCaseCause("被控盗窃罪")).toBe("盗窃罪");
  });

  it("extracts region (earliest wins)", () => {
    expect(extractRegion("案件在上海审理")).toBe("上海");
    expect(extractRegion("上海与北京两地")).toBe("上海");
  });

  it("extracts trial level by priority", () => {
    expect(extractTrialLevelPreference("一审判决后提起二审")).toBe("二审");
    expect(extractTrialLevelPreference("申请再审")).toBe("再审");
  });

  it("dedups and caps dispute keywords", () => {
    const kws = extractDisputeFocusKeywords("货款、违约金、违约金、利息争议");
    expect(kws).toContain("货款");
    expect(kws).toContain("违约金");
    expect(kws).toContain("利息");
    expect(new Set(kws).size).toBe(kws.length);
    expect(kws.length).toBeLessThanOrEqual(8);
  });

  it("extracts expected elements from fixture (parity with backend)", () => {
    const profile = buildSearchProfileFromRaw(FAKE_RAW_CASE);
    expect(profile.case_cause).toBe("买卖合同纠纷");
    expect(profile.region).toBe("上海");
    expect(profile.trial_level_preference).toBe("二审");
    expect(profile.dispute_focus_keywords).toContain("货款");
    expect(profile.dispute_focus_keywords).toContain("违约金");
  });
});
