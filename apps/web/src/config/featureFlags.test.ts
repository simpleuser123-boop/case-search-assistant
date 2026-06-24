import { afterEach, describe, expect, it, vi } from "vitest";

import {
  isAccountSystemEnabled,
  isBillingEnabled,
  isBulkImportEnabled,
  isCaseFavoriteEnabled,
  isCaseListEnabled,
  isCasebookEnabled,
  isDraftingEnabled,
  isExpandedSearchEnabled,
  isIntakeAiExtractionEnabled,
  isIntakeEnabled,
  isListExportEnabled,
  isM1M5AcceptanceEnabled,
  isPermissionTieringEnabled,
  isReportTemplateEnabled,
  isSearchHistoryEnabled,
  isStatuteSearchEnabled,
  isTeamSharingEnabled,
  isTeamWorkspaceEnabled,
} from "./featureFlags";

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("feature flags", () => {
  it("keeps M1-M5 acceptance mode off by default", () => {
    vi.stubEnv("VITE_ENABLE_M1_M5_ACCEPTANCE", "false");

    expect(isM1M5AcceptanceEnabled()).toBe(false);
    expect(isExpandedSearchEnabled()).toBe(false);
    expect(isSearchHistoryEnabled()).toBe(false);
    expect(isCaseFavoriteEnabled()).toBe(false);
    expect(isCaseListEnabled()).toBe(false);
    expect(isListExportEnabled()).toBe(false);
    expect(isReportTemplateEnabled()).toBe(false);
    expect(isAccountSystemEnabled()).toBe(false);
    expect(isTeamWorkspaceEnabled()).toBe(false);
    expect(isPermissionTieringEnabled()).toBe(false);
    expect(isTeamSharingEnabled()).toBe(false);
    expect(isBulkImportEnabled()).toBe(false);
    expect(isBillingEnabled()).toBe(false);
  });

  it("opens completed M1-M5 frontend UI flags with one local acceptance switch", () => {
    vi.stubEnv("VITE_ENABLE_M1_M5_ACCEPTANCE", "true");

    expect(isM1M5AcceptanceEnabled()).toBe(true);
    expect(isExpandedSearchEnabled()).toBe(true);
    expect(isSearchHistoryEnabled()).toBe(true);
    expect(isCaseFavoriteEnabled()).toBe(true);
    expect(isCaseListEnabled()).toBe(true);
    expect(isListExportEnabled()).toBe(true);
    expect(isReportTemplateEnabled()).toBe(true);
    expect(isAccountSystemEnabled()).toBe(true);
    expect(isTeamWorkspaceEnabled()).toBe(true);
    expect(isPermissionTieringEnabled()).toBe(true);
    expect(isTeamSharingEnabled()).toBe(true);
    expect(isBulkImportEnabled()).toBe(true);
    expect(isBillingEnabled()).toBe(true);
  });

  it("keeps E4 intake flags off by default", () => {
    expect(isIntakeEnabled()).toBe(false);
    expect(isIntakeAiExtractionEnabled()).toBe(false);
  });

  it("keeps E5 statute search flag off by default", () => {
    expect(isStatuteSearchEnabled()).toBe(false);
  });

  it("keeps E5 statute search flag decoupled from the M1-M5 acceptance switch", () => {
    vi.stubEnv("VITE_ENABLE_M1_M5_ACCEPTANCE", "true");
    expect(isStatuteSearchEnabled()).toBe(false);
  });

  it("keeps E5 statute search flag decoupled from the intake flag", () => {
    vi.stubEnv("VITE_ENABLE_INTAKE", "true");
    expect(isStatuteSearchEnabled()).toBe(false);
  });

  it("opens statute search only when its own flag is on", () => {
    vi.stubEnv("VITE_ENABLE_STATUTE_SEARCH", "true");
    expect(isStatuteSearchEnabled()).toBe(true);
    expect(isIntakeEnabled()).toBe(false);
  });

  it("keeps E-series intake flag decoupled from the M1-M5 acceptance switch", () => {
    vi.stubEnv("VITE_ENABLE_M1_M5_ACCEPTANCE", "true");
    expect(isIntakeEnabled()).toBe(false);
    expect(isIntakeAiExtractionEnabled()).toBe(false);
  });

  it("opens intake only when its own flag is on; AI extraction stays off", () => {
    vi.stubEnv("VITE_ENABLE_INTAKE", "true");
    expect(isIntakeEnabled()).toBe(true);
    expect(isIntakeAiExtractionEnabled()).toBe(false);
  });

  it("keeps E6 drafting flag off by default", () => {
    expect(isDraftingEnabled()).toBe(false);
  });

  it("keeps E6 drafting flag decoupled from the M1-M5 acceptance switch", () => {
    vi.stubEnv("VITE_ENABLE_M1_M5_ACCEPTANCE", "true");
    expect(isDraftingEnabled()).toBe(false);
  });

  it("keeps E6 drafting flag decoupled from intake / statute flags", () => {
    vi.stubEnv("VITE_ENABLE_INTAKE", "true");
    vi.stubEnv("VITE_ENABLE_STATUTE_SEARCH", "true");
    expect(isDraftingEnabled()).toBe(false);
  });

  it("opens drafting only when its own flag is on; other E-series flags stay off", () => {
    vi.stubEnv("VITE_ENABLE_DRAFTING", "true");
    expect(isDraftingEnabled()).toBe(true);
    expect(isIntakeEnabled()).toBe(false);
    expect(isStatuteSearchEnabled()).toBe(false);
  });

  it("keeps E7 casebook flag off by default", () => {
    expect(isCasebookEnabled()).toBe(false);
  });

  it("keeps E7 casebook flag decoupled from the M1-M5 acceptance switch", () => {
    vi.stubEnv("VITE_ENABLE_M1_M5_ACCEPTANCE", "true");
    expect(isCasebookEnabled()).toBe(false);
  });

  it("keeps E7 casebook flag decoupled from intake / statute / drafting flags", () => {
    vi.stubEnv("VITE_ENABLE_INTAKE", "true");
    vi.stubEnv("VITE_ENABLE_STATUTE_SEARCH", "true");
    vi.stubEnv("VITE_ENABLE_DRAFTING", "true");
    expect(isCasebookEnabled()).toBe(false);
  });

  it("opens casebook only when its own flag is on; other E-series flags stay off", () => {
    vi.stubEnv("VITE_ENABLE_CASEBOOK", "true");
    expect(isCasebookEnabled()).toBe(true);
    expect(isIntakeEnabled()).toBe(false);
    expect(isStatuteSearchEnabled()).toBe(false);
    expect(isDraftingEnabled()).toBe(false);
  });
});
