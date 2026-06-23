function readBooleanEnv(value: unknown, defaultValue: boolean) {
  if (typeof value !== "string") {
    return defaultValue;
  }

  const normalized = value.trim().toLowerCase();
  if (["1", "true", "yes", "on"].includes(normalized)) {
    return true;
  }
  if (["0", "false", "no", "off"].includes(normalized)) {
    return false;
  }

  return defaultValue;
}

// 本机 M1-M5 验收模式：只作为开发机/验收机的便捷总开关。
// 代码默认仍是 false；.env.example 也保持逐项 false。开启后只放出已完成的 M1-M5
// 前端 UI 能力，不开启 E 系列多产品入口，也不影响 weighted rerank 默认值。
export function isM1M5AcceptanceEnabled() {
  return readBooleanEnv(import.meta.env.VITE_ENABLE_M1_M5_ACCEPTANCE, false);
}

function readM1M5UiFlag(value: unknown, defaultValue = false) {
  if (typeof value === "string") {
    return readBooleanEnv(value, defaultValue);
  }
  if (isM1M5AcceptanceEnabled()) {
    return true;
  }
  return defaultValue;
}

export function isExpandedSearchEnabled() {
  return readM1M5UiFlag(import.meta.env.VITE_ENABLE_EXPANDED_SEARCH);
}

// M4-2 检索历史与草稿恢复（F16）。默认 false。
export function isSearchHistoryEnabled() {
  return readM1M5UiFlag(import.meta.env.VITE_ENABLE_SEARCH_HISTORY);
}

// M4-3 案例收藏能力（F17）。默认 false。
export function isCaseFavoriteEnabled() {
  return readM1M5UiFlag(import.meta.env.VITE_ENABLE_CASE_FAVORITE);
}

// M4-4 类案清单组装（F17）。默认 false。
export function isCaseListEnabled() {
  return readM1M5UiFlag(import.meta.env.VITE_ENABLE_CASE_LIST);
}

// M4-5 类案清单导出（F17）。默认 false。
export function isListExportEnabled() {
  return readM1M5UiFlag(import.meta.env.VITE_ENABLE_LIST_EXPORT);
}

// M4-6 轻量报告模板生成（F18）。默认 false。
export function isReportTemplateEnabled() {
  return readM1M5UiFlag(import.meta.env.VITE_ENABLE_REPORT_TEMPLATE);
}

// M5-2 账号体系与认证骨架。默认 false。
export function isAccountSystemEnabled() {
  return readM1M5UiFlag(import.meta.env.VITE_ENABLE_ACCOUNT_SYSTEM);
}

// M5-3 团队空间与数据隔离。默认 false。
export function isTeamWorkspaceEnabled() {
  return readM1M5UiFlag(import.meta.env.VITE_ENABLE_TEAM_WORKSPACE);
}

// M5-4 权限分级与对象级访问控制。默认 false。
export function isPermissionTieringEnabled() {
  return readM1M5UiFlag(import.meta.env.VITE_ENABLE_PERMISSION_TIERING);
}

// M5-5 沉淀同步与团队共享。默认 false。
export function isTeamSharingEnabled() {
  return readM1M5UiFlag(import.meta.env.VITE_ENABLE_TEAM_SHARING);
}

// M5-6 批量导入。默认 false。
export function isBulkImportEnabled() {
  return readM1M5UiFlag(import.meta.env.VITE_ENABLE_BULK_IMPORT);
}

// M5-8 法院/法官倾向分析（F19）。默认 false。后端数据门禁未达标即便开启也会被 403 拦截。
export function isTendencyAnalysisEnabled() {
  return readM1M5UiFlag(import.meta.env.VITE_ENABLE_TENDENCY_ANALYSIS);
}

// E4-4 案情录入端入口（E-1 已冻结 ENABLE_INTAKE）。默认 false：关闭时不渲染录入端入口/路由。
// 与本机 M1-M5 验收总开关正交，不被其联动开启 —— 录入端只看自己的 VITE_ENABLE_INTAKE。
export function isIntakeEnabled() {
  return readBooleanEnv(import.meta.env.VITE_ENABLE_INTAKE, false);
}

// E4 案情录入端「服务端 AI 增强抽取」子开关（正交于 ENABLE_INTAKE）。默认 false：E4 无 on 路径。
export function isIntakeAiExtractionEnabled() {
  return readBooleanEnv(import.meta.env.VITE_ENABLE_INTAKE_AI_EXTRACTION, false);
}

// E5-5 法条法规检索入口（E-1 已冻结 ENABLE_STATUTE_SEARCH，E5-4 实现后端 on 路径，E5-5 实现前端 on 路径）。
// 默认 false：关闭时不渲染法条检索入口/路由，DOM 无任何法条检索页可达入口，也不渲染类案结果页的「跳法条」入口。
// 与 VITE_ENABLE_M1_M5_ACCEPTANCE / VITE_ENABLE_INTAKE 正交，互不联动 —— 只看自己的 VITE_ENABLE_STATUTE_SEARCH。
// 开启后：查询态只存浏览器内存，只 POST 白名单字段；展示 StatuteRef（条文只来自语料、必带 text_id 锚点，
// 前端不生成/补全/改写条文）与互跳 CandidateRef（无正文、带来源锚点）。
export function isStatuteSearchEnabled() {
  return readBooleanEnv(import.meta.env.VITE_ENABLE_STATUTE_SEARCH, false);
}

// M5-9 商业化闭环（套餐/试用/计费/续费意愿）。默认 false。支付凭据绝不由本工具代填/代管/代存。
// 后端 ENABLE_BILLING=false 时即便前端开启也会被 403 拦截。
export function isBillingEnabled() {
  return readM1M5UiFlag(import.meta.env.VITE_ENABLE_BILLING);
}

// E6-3 文书工作台入口（E-1 已冻结 ENABLE_DRAFTING，E6-2 后端 on 路径，E6-3 前端 on 路径）。
// 默认 false：关闭时不渲染文书工作台入口/路由，DraftingPage 内部再判一次（双重门控）即返回 null。
// 与 VITE_ENABLE_M1_M5_ACCEPTANCE / VITE_ENABLE_INTAKE / VITE_ENABLE_STATUTE_SEARCH 正交，互不联动——
// 只看自己的 VITE_ENABLE_DRAFTING（不走 readM1M5UiFlag，验收总开关不会联动开启它）。
// 「只组装锚定来源、不起草结论」是结构性红线，不引入任何 AI 起草开关。
export function isDraftingEnabled() {
  return readBooleanEnv(import.meta.env.VITE_ENABLE_DRAFTING, false);
}

// E7-3 案件协作工作台入口（E-1 已冻结 ENABLE_CASEBOOK，E7-2 后端 on 路径，E7-3 前端 on 路径）。
// 默认 false：关闭时不渲染协作台入口/路由，CasebookPage 内部再判一次（双重门控）即返回 null。
// 与 VITE_ENABLE_M1_M5_ACCEPTANCE / VITE_ENABLE_INTAKE / VITE_ENABLE_STATUTE_SEARCH /
// VITE_ENABLE_DRAFTING 正交，互不联动——只看自己的 VITE_ENABLE_CASEBOOK（不走 readM1M5UiFlag，
// 验收总开关不会联动开启它）。
// 「只归集锚定引用、不起草不下结论」是结构性红线，不引入任何 AI 综述/归纳/预测开关。
export function isCasebookEnabled() {
  return readBooleanEnv(import.meta.env.VITE_ENABLE_CASEBOOK, false);
}
