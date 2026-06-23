import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Vitest 配置内联于 Vite（Day0 §4.2：测试命令能跑示例测试）
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // 后端 /api 反代到 FastAPI；Day 1 接入真实接口前可用 mock
    proxy: {
      "/api": { target: "http://localhost:8000", changeOrigin: true },
    },
  },
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    css: false,
  },
});
