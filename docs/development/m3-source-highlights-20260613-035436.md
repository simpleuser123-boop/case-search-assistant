# M3-5 相似片段高亮与关键段落定位 — 验收报告

- 步骤：M3-5
- 生成时间：2026-06-13 03:54:36 (+08:00)
- 结论：**GO**

## 1. 前置门禁

| 前置步骤 | 产物 | 结论 |
| --- | --- | --- |
| M3-1 阅读入口契约 | m3-reading-entry-contract-20260612-194511 | GO |
| M3-2 裁判要旨闭环 | m3-holding-summary-closure-20260612-202913 | GO |
| M3-3 争议焦点/关键要素 | m3-issue-focus-elements-20260612-212111 | GO |
| M3-4 事实对比 | m3-fact-alignment-20260613-021821 | GO |
| M2-2 来源锚点闭环 | m2-source-anchor-closure-20260612-104120 | GO |

五项前置全部 GO，门禁通过。`ENABLE_WEIGHTED_RERANK` 维持默认 `false`，未改动搜索排序、候选召回、source selection、rerank 默认开关或 M2 扩展检索默认行为。

## 2. similarity_highlights 结构

后端 `SimilarityHighlight`（`apps/api/app/schemas.py`）与派生逻辑（`apps/api/app/summary/highlights.py`）实现最小结构，仅承载元数据，不含任何正文：

| 字段 | 说明 |
| --- | --- |
| highlight_id | 稳定标识 `hl_<module>_<chunk>_<index>` |
| case_id | 来源案例标识（最小锚点字段之一） |
| source_chunk_id | 来源片段标识（最小锚点字段之一） |
| anchor_type | 锚点类型，默认 `detail_chunk` |
| related_module | 关联模块：holding_summary / issue_focus / key_elements |
| display_status | available / degraded |
| degrade_reason | 脱敏 reason code，可空 |

派生规则：仅从 `generation_status == "generated"` 的已锚定阅读模块收集锚点；强制要求 `case_id + source_chunk_id` 且 `case_id` 必须等于当前案例（跨案例锚点被过滤）；同模块去重；上限 12 条；当 `source_chunk_id` 不能在详情 chunk 中解析为可导航片段时，保留该高亮但标记 `degraded` + `source_chunk_unavailable`。

## 3. 前端详情页定位链路

`apps/web/src/lib/sourceHighlights.ts` 提供纯函数 `navigateToSourceChunk`（注入式 `resolveTarget`，可单测、永不向调用方抛错），`apps/web/src/components/details/CaseDetailDrawer.tsx` 接线三模块 + 事实对比的"查看来源片段"入口：

- 裁判要旨 → `openHoldingSource` → 跳转来源片段
- 争议焦点 → `openIssueFocusSource` → 跳转来源片段
- 关键要素 → `openKeyElementsSource` → 跳转来源片段
- 事实对比 → `openFactAlignmentSource` → 跳转来源片段

来源片段以 `source-chunk-<encoded_chunk_id>` 作为锚点元素 id，跳转通过 `scrollIntoView`。

## 4. 边界与降级

- 高亮只定位来源，不改写正文，不输出法律结论。
- 不使用 qrels / label / relevance / query id / case id 做运行时高亮特判。
- 高亮状态不参与主结果排序。
- 降级 reason code（脱敏）：`missing_source_anchor`、`source_chunk_unavailable`、`highlight_target_missing`、`navigation_failed`。
- 高亮定位失败安全降级，不影响详情页基础展示，不造成详情页/主结果白屏。
- 日志仅记录 count / anchor_type / related_module / status / reason_code，不记录正文；前端导航日志额外排除 case_id 与正文。

## 5. 正文泄露检查

| 检查项 | 结果 |
| --- | --- |
| 高亮 payload 含正文 | 否 |
| 报告 / JSON 含正文 | 否 |
| 日志含正文 | 否 |
| 测试快照含正文 | 否 |
| 专项防泄露测试 | 有（`test_no_body_text_leaks_into_highlight_payload` 植入秘密串并断言不出现在序列化输出） |

`highlights.py` 中对 chunk `text` 的读取仅用于"是否为空"的可导航性判定，从不写入输出结构。

## 6. 本次会话的磁盘修复

验证过程中发现两份前端文件在磁盘挂载上被截断（写入未完整刷盘），导致 esbuild transform 与 `tsc` 失败：

- `apps/web/src/components/details/CaseDetailDrawer.tsx`：截断于第 1311 行字符串中途（unterminated string literal）。
- `apps/web/src/types/search.ts`：截断于第 236 行 `export interface SearchApiErrorDetail {`（缺接口体与闭合）。

已按完整内容恢复两份文件，仅补回被截断的尾部，未改动任何 M3-5 逻辑。恢复后 esbuild / tsc / vite 全部通过。

## 7. 验证命令与结果

| 命令 | 结果 |
| --- | --- |
| `cd apps/api; pytest tests/test_summary_service.py tests/test_search_api_fallback_smoke.py tests/test_feature_flag_rollback.py tests/test_m3_source_highlights.py` | 29 passed |
| `cd apps/web; npm run test`（vitest run） | 63 passed（6 files） |
| `cd apps/web; tsc -b` | passed |
| `cd apps/web; vite build` | 103 modules transformed；clean outDir 构建成功 |

注：`npm run build` 默认 outDir 在清理 Windows 侧生成的 `dist/` 时触发 `EPERM`，为挂载环境产物，与代码无关；改用干净 outDir 后生产包构建成功。

## 8. 浏览器验收

VM 内无可用浏览器（无 chromium，playwright 浏览器未安装，与前序步骤一致：`browser_available: false`）。以 jsdom 级验收替代，`SearchPage.test.tsx` + `sourceHighlights.test.ts` 覆盖：

- 详情抽屉正常打开。
- `source_chunk_id` 与"打开原文"链接正常渲染。
- 裁判要旨、争议焦点、关键要素均带"查看来源片段"入口（按钮数 > 0）。
- 无来源内容不展示（安全降级）。
- 详情加载失败时主结果仍可见并可重试（高亮故障不致白屏）。
- 高亮导航单测覆盖全部四类降级 reason code。
- console error count：jsdom 运行无未捕获错误（测试全绿）。

## 9. 验收结论

- 高亮锚点至少包含 `case_id` 与 `source_chunk_id`：满足。
- 关联模块到来源片段的跳转可用：满足。
- 高亮不可用时安全降级：满足。
- 报告 / JSON / 日志 / 测试快照无正文泄露：满足。
- 不影响基础搜索与详情页基础展示：满足。

止损规则均未触发（高亮锚点稳定、无正文泄露、高亮故障不致详情页/主结果不可用）。

**M3-5 结论：GO。** 下一步新会话标题：类案检索助手 M3-6 案例对比视图受控入口。
