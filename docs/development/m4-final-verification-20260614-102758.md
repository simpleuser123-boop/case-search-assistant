# M4-8 验收报告：M4 工作流沉淀验收与 M5 入口结论

- 生成时间：2026-06-14T10:27:58+08:00
- 步骤：M4-8（M4 收尾验收 + M5 入口判定）
- 配套产物：`m4-release-gate-20260614-102758.json`、`m4-parameter-version-20260614-102758.json`

## 一、三类 Go/No-Go 结论

| 结论项 | 判定 |
| --- | --- |
| 基础搜索继续可用 | **GO** |
| M4 工作流沉淀完成 | **GO** |
| M5 入口 | **GO** |

- 是否需要回滚：否。
- 下一步新会话标题：**类案检索助手 M5-1 商业化扩展入口合同**。

## 二、M3-8 入场证据复核

来源 `m3-release-gate-20260613-072734.json` / `m3-final-verification-20260613-072734.md` 的 conclusions：

- 基础搜索 = GO，M3 阅读提效 = GO，M4 入口 = GO，rollback_required = false。
- `ENABLE_WEIGHTED_RERANK=false`（M3-8 已冻结）；M3-1~M3-7 七步 gate 全 GO。
- 来源锚点最小字段 = case_id + source_chunk_id，未锚定 AI 内容已降级或隐藏。
- qrels/label/relevance 未进运行时；无 query/case id 特判；warm P95=820ms<3s。

M3-8 入场条件全部成立，未发生回归。

## 三、M4-1 至 M4-7 能力状态汇总

逐份 .md / .json 产物复核，七步 gate 结论均为 GO：

| 步骤 | 能力 | 产物前缀 | 实现档位 | 结论 |
| --- | --- | --- | --- | --- |
| M4-1 | 工作流沉淀入口合同 | m4-workflow-entry-contract-20260613-090354 | 合同冻结 + 字段白名单 + 后端开关位声明 | GO |
| M4-2 | 检索历史与草稿恢复 | m4-search-history-draft-20260613-103734 | 纯前端 localStorage，后端 0 改动 | GO |
| M4-3 | 案例收藏能力 | m4-case-favorite-20260613-113516 | 纯前端 localStorage，后端 0 改动 | GO |
| M4-4 | 类案清单组装 | m4-case-list-20260613-211225 | 纯前端 localStorage，后端 0 改动 | GO |
| M4-5 | 类案清单导出 | m4-list-export-20260614-004136 | 纯前端 Blob 下载，后端 0 改动 | GO |
| M4-6 | 轻量报告模板生成 | m4-report-template-20260614-014251 | 纯前端组装 + 元数据 only，后端 0 改动 | GO |
| M4-7 | 团队复用能力评估 | m4-team-reuse-assessment-20260614-015309 | 仅评估 + 字段位声明，零业务代码 | GO |

关键事实：M4-2~M4-6 全部沉淀能力都是**纯前端 localStorage、单浏览器、单用户、零服务端持久层**；M4-1/M4-7 仅冻结合同与预留字段位。后端 `config.py` 仅新增 6 个默认 `false` 的开关位，无任何业务行为落地。

## 四、硬门禁复核

| 检查项 | 结果 | 证据 |
| --- | --- | --- |
| 持久层只含白名单内元数据/引用/用户自填短字段 | 通过 | 四个 storageKey（draft/history/favorite/list:v1）；`sanitize*` 反序列化只重建白名单键、主动丢弃非白名单键（含潜在正文） |
| 用户原始案情未上送服务端持久层（仅本地、可清除） | 通过 | 草稿/历史/收藏/清单正文只在浏览器 localStorage；合同 `user_raw_case_persisted_on_server=false`；后端 db.py 无业务表 |
| 清单/导出/报告案例侧 AI 内容 100% 可追溯 case_id + source_chunk_id | 通过 | `sanitizeListAnchors`/`sanitizeReportAnchors`/`filterAnchoredFragments` 只留齐全且归属本案锚点；无锚点不进交付物 |
| 无来源内容未进入清单/导出/报告 | 通过 | 报告默认无片段=纯元数据骨架；无锚点项降级展示「来源引用暂缺」，不伪造 source_chunk_id |
| M4 能力未改变主排序与 M2/M3 默认行为 | 通过 | `getFavoriteSelection`/`getListSelection` 只读引用布尔，不回写排序特征；手动排序只改清单展示；build 118 modules 与 M4-6 持平 |
| qrels/label/relevance 未进入历史/收藏/清单/导出/报告/排序 | 通过 | M4 lib 源码扫描 0 命中 qrels/relevance；`label` 仅作 UI/合同字段名 |
| query id / case id 未用于运行时特判 | 通过 | `case_id ===` 命中全为去重/查找（判断本案是否已收藏/在清单内），属引用关系处理，非排序特判；无 `query_id ===` |

## 五、隐私与正文泄露扫描（含持久层与导出文件）

对全部 `m4-*.md` / `m4-*.json` 产物 + M4 新增前端 lib 源码 + 实际导出文件执行扫描：

- 裁判文书正文标志（经审理查明/本院认为/公诉机关指控/事实和理由如下/判决如下）：在产物中**0 实质命中**。唯一字符串命中为 M3-2 能力名「裁判要旨摘要」（步骤标签，非正文）。
- M4 新增 lib（searchHistory/caseFavorite/caseList/caseListExport/reportTemplate）：**0 命中**正文字段读取（judgment_long_text/case_fact_body/candidate_body/chunk_body）。
- 脱敏日志结构核对：`case_favorite_action`/`case_list_action`/`case_list_export`/`report_template_action`/`citation_copy_action` 仅输出 event/surface/status/reason_code/count（导出加 format、报告加 section_count/item_count），**无正文、无案号、无 note/tag/title、无 query**。
- 实际导出文件抽查（M4-5/M4-6 acceptance 测试中故意向来源对象塞入 5 类正文 + raw_query）：Markdown/CSV/报告 .md 三类输出 **bodyHits=[]、absHits=[]、rawQueryLeak=false**，且含全 4 行免责说明。
- 测试运行期可见的导出/报告日志 JSON（`{"event":"case_list_export",...}` / `{"event":"report_template_action",...}`）确认仅含元数据字段。

结论：持久层、开发报告、JSON 产物、日志、测试快照、导出文件中**均无正文型内容泄露**。

## 六、禁用文案扫描（含导出文件与报告）

- 「已查全」「保证无遗漏」「查全率」：**0 实质命中**。命中行全为否定句（合同「不输出"已查全/保证无遗漏/查全率"」）或导出禁用词守门数组 `forbidden_phrase_guard` 本身。
- 胜诉概率/败诉概率/胜诉率/败诉率/确定性法律结论：**0 实质命中**。M4-6 命中行为否定句「**不输出**胜诉/败诉概率或确定性法律结论」。
- 运行时主动护栏：导出 `containsForbiddenExportPhrase` + 报告 `reportRenderHasForbiddenPhrase` 复用 11 词禁用表，免责文案自身通过校验。
- 报告**不自动起草**代理词/诉状/答辩状/庭审提纲；强制 4 行免责（仅元数据不含正文 / 阶段性可能未覆盖 / 不提供胜负判断不构成法律意见不可直接作法律文书 / 须律师结合原文人工复核）。

结论：无绝对覆盖话术、无胜负概率、无确定性法律结论，未把清单/导出/报告包装成法律意见或诉讼结果判断。

## 七、复跑核心门禁（测试证据）

### 后端 pytest（核心门禁 7 文件）

命令：`python3 -m pytest tests/test_m1_3_candidate_comparison.py tests/test_m1_3_legal_candidate_robustness.py tests/test_feature_flag_rollback.py tests/test_health.py tests/test_search_api_fallback_smoke.py tests/test_summary_service.py tests/test_performance_smoke.py`

结果：**60 passed**，exit=0。

### 后端 M4 + M3 focused 测试（按要求纳入）

- M4 focused：`test_m4_workflow_entry_contract.py` + `test_m4_team_reuse.py` → **24 passed**。
- M3 focused：`test_m3_fact_alignment.py` + `test_m3_holding_summary.py` + `test_m3_issue_focus.py` + `test_m3_source_highlights.py` → **29 passed**。

### 前端

- `vitest run`（20 文件分批，规避 VM 45s 窗口）：lib 80 + lib/services 55 + pages-accept 25 + HomePage/ListExport 16 + Report/SearchHistory 15 + SearchPage 33 = **224 passed**，0 failed。
- `tsc -b`：exit=0。
- `vite build`：**118 modules transformed**，built OK，exit=0（与 M4-6 持平，印证 M4-7 零业务改动）。

> 环境说明（非阻断）：后端 `.venv` 为 Windows 侧、Linux VM 不可直用，本轮按既有解法在 VM 内重装依赖（pydantic 2.13.4 wheel / pydantic-settings / SQLAlchemy / pytest / httpx / starlette==0.37.2，fastapi==0.111.0 与 sqlmodel 用 `--no-deps`），`DATABASE_URL=sqlite` 规避 psycopg2，chromadb 惰性 import 不触发。`vite build` 输出到 /tmp 干净目录规避 Windows 侧 dist 清理 EPERM。临时 sqlite db 已删除。

## 八、性能与回滚验证

- 搜索链路 warm P95：`test_performance_smoke.py` 断言 `warm_response_total_duration_ms.p95 == 820` 且 `p95_under_3s is True` → **820ms < 3s，通过**。
- M4 沉淀能力不阻断主链路：所有 M4 能力 flag-gated，关闭时不读写本地存储、不渲染入口；本地存储异常（隐私模式/配额/JSON 损坏）一律安全降级，不破坏主检索/阅读链路；导出/报告生成全程 try/catch，失败走 degraded/failed 安全态。
- 关闭/降级路径：每个 M4 能力都有可关闭开关，关闭后逐级回到 M3 末态（REPORT→LIST_EXPORT→CASE_LIST→CASE_FAVORITE→SEARCH_HISTORY→M3）。
- `ENABLE_WEIGHTED_RERANK` 未默认开启；6 个 M4 feature flag（含 ENABLE_TEAM_REUSE）默认全 false。
- `.env.example` 未把 weighted rerank 或 team reuse 默认改为 true（backend :23/:33-38 全 false，VITE :60-70 全 false）。

## 九、止损触发器复核

持久层/报告/日志/JSON/导出文件/测试快照泄露正文、用户原始案情上送服务端、清单/导出/报告引用无来源 AI 内容、绝对覆盖话术、胜负概率/确定性结论、报告自动起草法律文书、qrels/label/relevance 进入交付物或排序、query/case id 特判、weighted rerank 或 team reuse 默认开启、M3-8 核心门禁回归、M4 能力不可回滚或主链路不可用——**全部未触发**。

## 十、可见验收点说明

本环境 host↔VM 网络桥不可达，原生浏览器无法访问 VM 内 localhost；前端可见验收点通过 vitest + jsdom 真实组件树覆盖（CaseFavoriteAcceptance / CaseListAcceptance / ListExportAcceptance / ReportTemplateAcceptance / SearchHistoryDraftAcceptance / SearchPage 等共 224 测试全绿，console error=0）。此为沿用 M3-6 起的既有非阻断说明。

## 十一、结论与下一步

三类门禁全部 **GO**，无止损触发，无回归。允许进入 M5 商业化扩展。

- 基础搜索继续可用：**GO**。
- M4 工作流沉淀完成：**GO**。
- M5 入口：**GO**。
- 下一步新会话标题：**类案检索助手 M5-1 商业化扩展入口合同**。
