# M4-2 检索历史与草稿恢复 — 验收报告

- 步骤：M4-2（F16 检索历史草稿）
- 时间：2026-06-13 10:37:34 +08:00
- 结论：**GO**（允许进入 M4-3 案例收藏能力）
- 范围：纯前端实现，零后端改动；草稿与历史正文只存在于浏览器本地、可清除，绝不上送后端持久层。

## 1. 入场依据

- M4-1 工作流沉淀入口合同 **GO**：`docs/development/m4-workflow-entry-contract-20260613-090354.{md,json}`。
- 合同冻结：`search_history` / `search_draft` 由 `ENABLE_SEARCH_HISTORY` 控制，默认 `false`；草稿正文与历史条目正文为 local-only、可清除；服务端可持久字段仅脱敏字段（`query_session_id` / `input_hash` / `result_count` / `degraded_status` / `created_at` / `history_status` / `reason_code`），且合同标注为「如需」可选。
- 服务端档位决策：用户选择「纯前端零后端改动」。现有 analytics 已覆盖 `result_count` / `degraded`，本步不新增服务端持久层、不新增埋点事件。

## 2. 实现要点

### 2.1 Feature flag（默认安全态）

- 前端开关 `VITE_ENABLE_SEARCH_HISTORY`，读取器 `apps/web/src/config/featureFlags.ts:isSearchHistoryEnabled()`，默认 `false`。
- 关闭时：不读写任何浏览器本地存储、不渲染历史/草稿入口，页面回到 M3 末态，标准搜索默认行为不变。
- 后端 `ENABLE_SEARCH_HISTORY` 续 `false`（config.py 未改动）。

### 2.2 数据结构（均 local-only、可清除）

- `search_draft`（`apps/web/src/lib/searchHistory.ts`）：`draft_text`（未提交案情正文，仅本地）、`updated_at`。storageKey=`case-search:m4:search-draft:v1`。服务端可持久字段=空。
- `search_history_entry`：`id`（仅本地 React key / 删除用）、`query_text`（本地侧正文，仅用于重搜回填）、`query_preview`（截断展示）、`input_length`、`result_count`、`degraded`、`created_at`、可选 `title`。storageKey=`case-search:m4:search-history:v1`。最多 10 条，按 `created_at` 倒序，按 cleaned query 去重。

### 2.3 草稿规则

- 仅 flag 开启时自动保存；正文只落 `localStorage`，刷新（remount）可恢复。
- 用户可一键「清除草稿」。检索成功后自动清空草稿。
- 已恢复草稿在用户未改写前保持「已恢复」提示；改写后转为「已保存当前输入草稿」。

### 2.4 历史规则

- 展示足够识别的信息：时间、结果数、是否降级、可选自填标题、查询预览；不向后端上送原始案情。
- 提供「清除历史（清空全部，带二次确认）」与「删除单条」入口。

### 2.5 重搜规则

- `handleResearchFromHistory` 把历史正文回填后调用与首次检索完全相同的 `runSearch`：不绕过查询清洗、不绕过改写降级、不改主排序默认。
- 不按 query id / case id 做历史特判。

### 2.6 隐私规则

- 后端日志与埋点只记录脱敏字段；本步复用已在白名单内的 `has_draft_restored`（boolean），未新增任何携带正文的字段。
- `/api/events` 请求体经断言验证不含原始案情正文。
- 历史/草稿正文不写入服务端持久层、开发报告或测试快照（正文只在浏览器本地）。

## 3. 改动文件

新增：
- `apps/web/src/lib/searchHistory.ts`（纯函数 + 可注入 storage）
- `apps/web/src/lib/searchHistory.test.ts`
- `apps/web/src/components/search/SearchHistoryPanel.tsx`
- `apps/web/src/pages/SearchHistoryDraftAcceptance.test.tsx`

修改：
- `apps/web/src/config/featureFlags.ts`（新增 `isSearchHistoryEnabled`）
- `apps/web/src/vite-env.d.ts`（新增 `VITE_ENABLE_SEARCH_HISTORY` 类型）
- `apps/web/src/pages/SearchPage.tsx`（flag-gated 草稿/历史接线 + 侧栏面板）

后端改动：**0 个文件**。

## 4. 验证

- 前端 `tsc -b`：通过（exit 0）。
- 前端 `vite build`：通过，109 modules（M4-1 末态 107 → +SearchHistoryPanel +searchHistory）。
- 前端测试全量绿（分两批跑，规避 VM 45s 窗口）：批1 SearchPage 33 + HomePage 9 + CitationCopy 7 = 49；批2 caseCompare/citationCopy/sourceHighlights/CaseCompareAcceptance/analytics/feedbackApi/searchApi = 52；新增 M4-2 = searchHistory.test 13 + SearchHistoryDraftAcceptance 7 = 20。合计 121 passed。
- 后端 pytest（回归）：`tests/test_health.py tests/test_search_api_fallback_smoke.py tests/test_feature_flag_rollback.py` → **18 passed**（`DATABASE_URL=sqlite`，临时 db 跑后删除）。
- 默认关闭回归断言全部仍通过：SearchPage 无 `setItem` 调用、无 console.log/error；CitationCopyAcceptance 无历史/收藏按钮；HomePage 原始 query 不入 storage。

### 浏览器验收说明

host↔VM 浏览器桥不可达（沿用 M3-6/M3-7 结论），可见验收点改用 vitest + jsdom 真实组件树在 mock 路径覆盖：草稿恢复、历史展示、重搜、清除历史/草稿、`/api/events` 请求体无原始案情、console error=0。

## 5. 验收对照

| 验收点 | 结果 |
| --- | --- |
| 草稿可本地恢复、可清除 | PASS |
| 历史可展示、可重搜、可清除 | PASS |
| 原始案情不上送后端持久层 | PASS |
| 历史/草稿不改变主排序 | PASS |
| 无正文泄露 | PASS |

## 6. 止损检查（均未触发）

- 原始案情上送服务端持久层：否
- 历史/草稿正文进入服务端日志或报告：否
- 历史影响主排序：否

结论：**GO**，可进入 M4-3。
