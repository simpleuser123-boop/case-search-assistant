# M3-7 复制案号与引用格式边界 · Go/No-Go 报告

- 步骤：M3-7 复制案号与引用格式边界
- 时间：2026-06-13 06:28:14
- 结论：**GO**
- 范围：仅前端（apps/web）。后端 Python 改动文件数 = 0（本会话 apps/api 无任何文件改动，mtime 均早于会话开始）。

## 1. 入场依据（已确认仍成立）

| 前置门禁 | 状态 |
| --- | --- |
| M2-8 基础搜索 | GO |
| M2 可信检索 | GO |
| M3 入口 | GO |
| M3-1 阅读入口合同 | GO |
| M3-2 裁判要旨摘要闭合 | GO |
| M3-3 争议焦点与关键要素 | GO |
| M3-4 相似事实对比 | GO |
| M3-5 相似片段高亮 | GO |
| M3-6 案例对比视图受控入口 | GO |
| ENABLE_WEIGHTED_RERANK | 默认 false，未改动 |

来源/召回/rerank 默认、扩展检索默认、在线排序均未改动。

## 2. 实现方式

复制能力采用**纯前端 + 元数据only**实现：在 `lib/citationCopy.ts` 中定义 `citation_copy` 数据结构与格式化 helper、剪贴板写入动作、脱敏日志；`components/results/CopyCitationButton.tsx` 为单一可复用复制控件，状态仅存活于组件本地 React state，2.4s 后自动复位，不落库、不跨会话、不保存任何历史。

`citation_copy` 数据结构字段（仅元数据）：`case_id`、`case_number`、`court`、`trial_level`、`judgment_date`、`citation_format`、`copy_status`。

引用格式 `citation_format` 仅由元数据拼装，顺序为「法院 案号 （审级） 裁判日期」，空字段自动剔除；它是单行引用，不是清单、不是报告。

## 3. 受控入口与边界

| 规则 | 落地 |
| --- | --- |
| 结果卡片 | 「复制案号」按钮，kind=case_number |
| 详情页 | 「复制引用格式」+「复制案号」按钮，kind=citation / case_number |
| 对比视图 | 每个案例 chip 上「复制引用」按钮，kind=citation，仅单案 |
| 复制内容 | 仅元数据与基础引用格式 |
| 导出文件 | 无 |
| 复制历史 | 无 |
| 收藏 / 类案清单 / 报告 / 分析结论 | 未实现 |
| 持久化 | 无；仅 ephemeral 组件状态 |
| 影响排序 / 推荐 / 对比选择 | 无 |
| 胜诉 / 败诉 / 概率 / 确定性结论 | 无 |

## 4. 失败处理

剪贴板不可用（`navigator.clipboard` 缺失）时返回 `unavailable` + reason `clipboard_unavailable`，界面展示安全提示「复制不可用，请手动选择文本复制」；写入抛错时返回 `failed` + reason `clipboard_write_failed`。元数据缺失时不写剪贴板，返回 `unavailable` + `missing_case_number` / `missing_metadata`。任何失败都不影响主搜索、详情页与对比视图。

## 5. 埋点和日志

复制事件日志 `citation_copy_action` 仅含：`event`、`surface`（result_card/detail/compare）、`kind`、`status`、`reason_code`、`count`。不记录被复制文本、案号、引用正文、用户输入、摘要正文、片段正文。日志失败被吞掉，绝不打断阅读流。

## 6. 验证结果

| 命令 | 结果 |
| --- | --- |
| `npx tsc --noEmit`（web） | pass（exit 0） |
| `vitest run src/lib/citationCopy.test.ts` | 15 passed |
| `vitest run src/pages/CitationCopyAcceptance.test.tsx` | 7 passed |
| `npm run test`（全量，分批跑） | 10 files / 101 tests passed，0 回归（新增 22：lib 15 + acceptance 7） |
| `vite build`（干净目录） | pass，107 modules |
| API smoke（pytest 三项） | **18 passed**（形式确认补跑，见下） |

### API smoke 补跑说明（形式确认）

报告出具后按建议在本 VM 补跑三项 smoke 并通过：`pytest tests/test_health.py tests/test_search_api_fallback_smoke.py tests/test_feature_flag_rollback.py` → **18 passed**。

依赖此前 40s 时限内装不全，本次改为分步增量安装（pydantic / pydantic-settings / SQLAlchemy / pytest / httpx / starlette 正常装，fastapi 与 sqlmodel 用 `--no-deps` 装主包跳过卡网络的附属）；以 `DATABASE_URL=sqlite:///...` 规避 psycopg2（chromadb 为惰性导入，smoke 不触发，无需安装）。

结果符合「后端未改动」的预期——M3-7 为纯前端步骤，三项 smoke 覆盖健康检查 / 搜索 fallback / feature flag rollback 均通过，0 失败。临时 sqlite 文件已清理。

### 浏览器验收（jsdom 真实组件树）

原生浏览器验收因 host↔VM 网络桥不可达而无法进行；沿用既有 jsdom 方案，驱动真实组件树走内置 mock 数据路径覆盖可见验收点：

| 验收点 | 结果 |
| --- | --- |
| 结果卡片复制案号（仅元数据） | pass（写入剪贴板内容 = 案号本身） |
| 详情页复制基础引用格式 | pass（含法院+案号，无摘要/正文） |
| 对比视图复制单案引用 | pass |
| 复制成功状态（已复制） | pass |
| 剪贴板不可用安全提示 | pass，主结果存活 |
| 脱敏埋点（无正文键） | pass |
| 无导出/下载/历史/收藏/报告/清单入口 | pass |
| console error count | 0 |

## 7. 正文泄露检查

本报告与 JSON 仅含字段名、count、status、reason code、feature flag 状态、指标摘要与测试结果；不含原始 query、案情正文、候选正文、chunk 正文、裁判文书正文或用户自由文本。复制日志 `citation_copy_action` 同样只含 surface/kind/status/reason code/count。

## 8. 验收（全部满足）

- 复制能力只处理元数据和基础引用格式：**满足**
- 不保存复制正文和用户输入：**满足**
- 不引入 M4 工作流能力（导出/历史/收藏/清单/报告）：**满足**
- 不影响基础搜索、详情页和对比视图：**满足**

## 9. 止损（均未触发）

- 变相实现导出 / 历史 / 收藏 / 类案清单 / 报告：否
- 日志保存正文型内容：否

## 10. 结论

**GO**。M3-7 在受控边界内实现「复制案号与基础引用格式」：纯前端、仅元数据、不落库、不存历史、不导出、不生成清单或报告、不影响排序与对比选择、无正文泄露、无胜负结论。三处入口（结果卡片 / 详情页 / 对比视图）均到位，剪贴板不可用时安全降级且不破坏主链路。前端全量测试（101）与构建通过，新增 22 项 focused/acceptance 测试全绿。后端三项 pytest smoke 已在本 VM 形式确认通过（18 passed），与「后端未改动」预期一致。可进入 M3-8。

### 遗留与建议

- 三项 API smoke 已在本 VM 形式确认通过（18 passed），与「后端未改动」预期一致。
- 写入磁盘存在偶发截断（Windows 挂载刷盘问题）：本轮多个源文件与本报告/JSON 尾部均中招，已用 python 在干净边界重写并以 tsc(exit 0)/esbuild/vitest/json.load 复核，最终状态完整。
