# E5-5 前端法条检索页（gated） — 完成报告

- 时间戳：20260617-133220
- 结论：**GO**，`allow_enter_e5_6 = true`
- 前置：E5-4 `e5-statute-api-20260617-122654`（CONDITIONAL_GO，`allow_enter_e5_5=true`）

## 1. 本步完成的 E5 子目标

实现法条检索前端页（受 `VITE_ENABLE_STATUTE_SEARCH` 门控，默认 off 不渲染）：查询 → 带来源法条命中（`StatuteRef[]`，条文必带 `text_id` 锚点）→ 法条↔类案双向互跳。对标 E4-4 录入页落地范式：gated 页面 + 白名单请求体（逐字段显式取键、不 spread）。

## 2. 新增 / 修改文件

新增：
- `apps/web/src/services/statuteApi.ts` — 法条检索 API 客户端，三端点请求体白名单显式组装。
- `apps/web/src/services/statuteApi.test.ts` — 请求体白名单 / 注入正文仍只发白名单 / 互跳 CandidateRef 七字段断言（8 passed）。
- `apps/web/src/pages/StatutePage.tsx` — 法条检索页（双重 flag 门控）+ 命中展示（条文必带锚点）+ 法条→类案互跳。
- `apps/web/src/pages/StatutePage.test.tsx` — flag 门控 / 必带锚点 / 无锚点丢弃 / 互跳无正文 / 无存储调用（5 passed）。
- 本报告 `.md` / `.json`。

修改：
- `apps/web/src/config/featureFlags.ts` — 新增 `isStatuteSearchEnabled()`（读 `VITE_ENABLE_STATUTE_SEARCH`，默认 false；正交于 intake / M1-M5 验收开关）。
- `apps/web/src/config/featureFlags.test.ts` — 新增 4 条 statute flag 默认 / 正交 / on 路径断言。
- `apps/web/src/app/router.tsx` — gated 注册 `/statute` 路由。
- `apps/web/src/pages/HomePage.tsx` — gated 渲染「法条检索」入口。
- `apps/web/src/pages/SearchPage.tsx` — gated 渲染「跳法条检索」反向入口。
- `落地设计文档/20-E5法条检索分步骤系统提示词文档.md` — E5-5 小节补充。

## 3. 验证命令与结果

- `npx tsc -b` → pass（无输出）。
- `npx vite build` → **123 modules transformed**（E4 末态 121 + 2 个 gated statute 文件）。`dist` 目录 EPERM（host-mount 权限）经 `--outDir /tmp/e5_dist` 旁路后 `built in 5.05s`。
- `npx vitest run`（≤2 文件/批，逐批 exit=0）：
  - `statuteApi.test.ts` 8 passed
  - `StatutePage.test.tsx` 5 passed
  - `featureFlags.test.ts` 9 passed
  - `HomePage.test.tsx` 9 passed（回归）
  - `SearchPage.test.tsx` 33 passed（回归）
  - 触碰文件合计 **64 passed**。

## 4. 请求体白名单证据

`toStatuteSearchBody` 逐字段显式取 7 个白名单键（`case_cause/region/trial_level_preference/dispute_focus_keywords/query_text/mode/limit`），不 spread 任意对象。测试 `never carries raw case / PII keys even if injected`：即便把 `raw_case/raw_query/name/id_card` 挂到 profile 对象上，序列化后请求体仍只含白名单键，不含 `raw_case`、`13800138000`、`11010119900101001X` 等。互跳 `toStatuteByCaseBody` 仅 `case_id+mode+limit`，`toStatuteCasesBody` 仅 `statute_id+mode+limit`。

## 5. 条文必带锚点证据

- `hasDisplayableAnchor(ref)` 仅当 `statute_anchors` 含非空 `text_id` 时为真；`StatuteWorkspace` 用它 `.filter()` 命中列表——无锚点命中在前端被丢弃（与后端「无锚点不返回」一致）。
- 条文展示只渲染后端返回的 `statute.article_text`，前端不生成 / 不补全 / 不改写。
- 测试断言：两条命中（一条带锚点、一条无锚点）只渲染带锚点那条的条文与 `text_id`，命中计数为 1，无锚点命中的条文 `这条没有锚点` 与法名 `无锚点法` 均不出现在 DOM。

## 6. flag 门控验证

- 默认 `VITE_ENABLE_STATUTE_SEARCH=false`：`StatutePage` 渲染 `null`（`container.firstChild` 为 null）；router 不注册 `/statute`；HomePage / SearchPage 入口不渲染。
- `=true`：workspace 可见、`检索法条` 可用。
- 正交：M1-M5 验收总开关 on、intake flag on 均不联动放出法条入口（断言 `isStatuteSearchEnabled()` 仍 false）。

## 7. 隐私扫描结果

裁判正文渲染 **0** / 模型杜撰条文 **0** / PII 进请求体·URL·存储 **0** / 凭据 **0** / 禁用（胜负/营销）文案 **0** / 无锚点条文渲染 **0**。`localStorage/sessionStorage/IndexedDB/cookie` 仅出现在注释，运行时 `Storage.prototype.setItem/getItem` 断言未被调用。互跳 `Link` 仅携带 `case_id`（`encodeURIComponent`），不带正文 / PII。

## 8. 是否改变外部行为

未改后端 `apps/api/app/statute/*` 端点契约、未改 `/api/search`、`/api/search/expand`、E3 `InternalSearchService`、intake 端点；未改排序 / 召回 / source selection / rerank 默认；未新建后端产品包或端点。本步纯前端新增（gated）。

## 9. flag 默认值复核

`ENABLE_STATUTE_SEARCH` / `VITE_ENABLE_STATUTE_SEARCH` / `ENABLE_INTAKE` / `VITE_ENABLE_INTAKE` / `ENABLE_INTAKE_AI_EXTRACTION` / `ENABLE_DRAFTING` / `ENABLE_CASEBOOK` / `ENABLE_WEIGHTED_RERANK` 全部默认 `false`（`.env.example` 逐项核对）。

## 10. 法条语料覆盖范围

刑事（沿用 E5-2 JuDGE `law_corpus` 种子）；民事 / 行政未扩充。前端不假设覆盖范围、不杜撰条文，命中与条文均以后端返回为准。

## 11. 环境坑记录（VM stale-mount）

本步多次遇到「host Edit 后 VM mount 读到截断旧副本」（`featureFlags.ts` / `SearchPage.tsx` / `HomePage.tsx` / `router.tsx` 均中招，esbuild 报 Unterminated string / Unexpected EOF）。处理：对小文件用 VM heredoc 整文件重写；对大文件（SearchPage 1329 行）先确认 host 完整、VM 截断点之前与 host 逐字节一致，再用 Python 在 VM 侧丢弃尾部残行并 append 正确尾段（绝不 VM 读全量再写回，避免把截断写回 host）。修复后 build / 测试全绿。

## 下一步

**类案检索助手 E5-6 消费边界与护栏守门**
