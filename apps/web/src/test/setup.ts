import "@testing-library/jest-dom";

import { beforeEach, vi } from "vitest";

beforeEach(() => {
  vi.stubEnv("VITE_ENABLE_M1_M5_ACCEPTANCE", "false");
});
