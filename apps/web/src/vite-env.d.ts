/// <reference types="vite/client" />

interface ImportMetaEnv {
  // 本机 M1-M5 验收模式：打开已完成 M1-M5 前端 UI 能力；默认 false。
  readonly VITE_ENABLE_M1_M5_ACCEPTANCE?: string;
  readonly VITE_ENABLE_EXPANDED_SEARCH?: string;
  // M4-2: 检索历史与草稿恢复开关（默认 false / 关闭即回到 M3 末态）。
  readonly VITE_ENABLE_SEARCH_HISTORY?: string;
  // M4-3: 案例收藏开关（默认 false / 关闭即回到 M4-2 末态）。
  readonly VITE_ENABLE_CASE_FAVORITE?: string;
  // M4-4: 类案清单组装开关（默认 false / 关闭即回到 M4-3 末态）。
  readonly VITE_ENABLE_CASE_LIST?: string;
  // M4-5: 类案清单导出开关（默认 false / 关闭即回到 M4-4 末态）。
  readonly VITE_ENABLE_LIST_EXPORT?: string;
  // M4-6: 轻量报告模板生成开关（默认 false / 关闭即回到 M4-5 末态）。
  readonly VITE_ENABLE_REPORT_TEMPLATE?: string;
  // M5-2~M5-9：商业化扩展开关（默认 false / 关闭即回到上一阶段末态）。
  readonly VITE_ENABLE_ACCOUNT_SYSTEM?: string;
  readonly VITE_ENABLE_TEAM_WORKSPACE?: string;
  readonly VITE_ENABLE_PERMISSION_TIERING?: string;
  readonly VITE_ENABLE_TEAM_SHARING?: string;
  readonly VITE_ENABLE_BULK_IMPORT?: string;
  readonly VITE_ENABLE_TENDENCY_ANALYSIS?: string;
  readonly VITE_ENABLE_BILLING?: string;
  // E4-4: 案情录入端入口开关（E-1 冻结 / 默认 false / 关闭即不渲染录入入口/路由）。
  readonly VITE_ENABLE_INTAKE?: string;
  // E4: 录入端服务端 AI 增强抽取子开关（默认 false / E4 仅声明、零接线、无 on 路径）。
  readonly VITE_ENABLE_INTAKE_AI_EXTRACTION?: string;
  // 检索 API 客户端模式（api / mock）；录入端结果页复用同款来源标识。
  readonly VITE_SEARCH_API_MODE?: string;
  readonly VITE_SEARCH_API_TIMEOUT_MS?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
