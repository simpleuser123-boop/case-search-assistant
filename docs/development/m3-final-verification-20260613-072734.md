# M3-8 验收报告：M3 阅读提效验收与 M4 入口结论

- 生成时间：2026-06-13T07:27:34+08:00
- 步骤：M3-8（M3 收尾验收 + M4 入口判定）
- 配套产物：`m3-release-gate-20260613-072734.json`、`m3-parameter-version-20260613-072734.json`

## 一、三类 Go/No-Go 结论

| 结论项 | 判定 |
| --- | --- |
| 基础搜索继续可用 | **GO** |
| M3 阅读提效完成 | **GO** |
| M4 入口 | **GO** |

- 是否需要回滚：否。
- 下一步新会话标题：**类案检索助手 M4-1 工作流沉淀入口合同**。

## 二、前置门禁复核

M3-1 至 M3-7 七步产物全部存在，且各自 gate 结论均为 GO（逐份 JSON 复核）：

| 步骤 | 能力 | 产物前缀 | 结论 |
| --- | --- | --- | --- |
| M3-1 | 阅读入口合同 | m3-reading-entry-contract-20260612-194511 | GO |
| M3-2 | 裁判要旨摘要 | m3-holding-summary-closure-20260612-202913 | GO |
| M3-3 | 争议焦点与关键要素 | m3-issue-focus-elements-20260612-212111 | GO |
| M3-4 | 相似事实对比 | m3-fact-alignment-20260613-021821 | GO |
| M3-5 | 相似片段高亮 | m3-source-highlights-20260613-035436 | GO |
| M3-6 | 案例对比视图 | m3-case-compare-gate-20260613-054541 | GO |
| M3-7 | 复制案号与引用格式 | m3-citation-copy-boundary-20260613-062814 | GO |

- `ENABLE_WEIGHTED_RERANK` 保持 false：`apps/api/app/core/config.py:42`（`ENABLE_WEIGHTED_RERANK: bool = False`）、`.env.example:23`（`ENABLE_WEIGHTED_RERANK=false`）。
- 未修改 qrels、label、历史评测结果；本会话 git 工作树无业务代码改动。

## 三、M2-8 入场证据复核

来源 `m2-release-gate-20260612-191945.json` 的 conclusions / hard_gates：

- base_search = GO，m2_trusted_retrieval = GO，m3_entry = GO，rollback_required = false。
- `weighted_rerank_default_enabled = false`，`m1_3x_core_regression_detected = false`。
- `qrels_label_relevance_runtime_use_detected = false`，`query_or_case_id_special_case_detected = false`。
- source_anchor 最小字段 = case_id + source_chunk_id，未锚定 AI 内容已降级。

M2-8 入场条件全部成立，未发生回归。

## 四、硬门禁复核

| 检查项 | 结果 | 证据 |
| --- | --- | --- |
| 案例侧 AI 内容 100% 有 source_anchors | 通过 | M3-4/5/6 gate 字段 `case_side_visible_requires_source_anchors=true`、`facts_only_from_anchored_chunks=true`；`test_m3_fact_alignment.py:64` 断言 `case-side fact must be anchored` |
| source_anchors 可追溯 case_id + source_chunk_id | 通过 | gate 字段 `highlight_anchor_min_case_id_and_source_chunk_id=true`；`test_m3_fact_alignment.py:67` 断言含 source_chunk_id |
| 用户侧事实未持久化原文 | 通过 | gate 字段 `request_query_signal_persisted=false`、`user_side_raw_text_not_persisted=true`、`persisted_to_storage=false` |
| 无来源内容被隐藏/过滤/安全降级 | 通过 | `unanchored_case_fact_visible=false`、`unanchored_content_hidden=true`、`missing_source_anchor=degraded_safe_state` |
| 未改变主排序与 M2 默认行为 | 通过 | `affects_main_result_sorting=false`、`source_selection_changed=false`、`rerank_default_changed=false`、`online_sorting_changed=false` |
| qrels/label/relevance 未进入运行时 | 通过 | gate 字段 `uses_qrels=false`、`uses_label=false`、`uses_qrels_label_relevance_queryid_caseid_special_case=false`；源码 `label` 命中均为 UI 字段标签（法院/审级/案由），非 relevance label |
| query id / case id 未用于运行时特判 | 通过 | gate 字段 `uses_query_id_special_case=false`、`case_id_special_case_in_compare=false`；源码扫描无 `case_id===`/`query_id===` 值硬编码 |

## 五、隐私与正文泄露扫描

对全部 `m3-*.md` / `m3-*.json` 产物执行扫描：

- 超长中文连续文本块（≥120 字疑似正文）：**0 命中**。
- 裁判文书正文标志（经审理查明 / 本院认为 / 公诉机关指控 / 事实和理由如下 / 判决如下）：**0 命中**。
- 原始 query、案情正文、候选正文、chunk 正文、用户自由文本：未写入报告 / JSON / 日志 / 测试快照（gate 字段 `no_body_leakage=true`、`logs_source_text=false`、`source_fragment_body_saved_in_report_json=false`）。

结论：无正文泄露。

## 六、禁用文案扫描

- 「已查全」「保证无遗漏」「查全率」：**0 命中**。
- 胜诉概率 / 败诉概率 / 胜诉率 / 败诉率 / 确定性法律结论：**0 命中**。
- 主动护栏：`apps/web/src/components/details/CaseDetailDrawer.tsx:71` 的 `forbiddenReadingTerms` 列表（胜诉、败诉、概率、诉讼结果、确定性法律结论、风险评级）在渲染时过滤命中项，未把要旨 / 事实对比 / 高亮 / 对比视图包装成法律结论。

结论：无绝对覆盖话术、无胜负概率、无确定性法律结论。

## 七、复跑核心门禁（测试证据）

### 后端 pytest（指定 7 文件）

命令：`python3 -m pytest tests/test_m1_3_candidate_comparison.py tests/test_m1_3_legal_candidate_robustness.py tests/test_feature_flag_rollback.py tests/test_health.py tests/test_search_api_fallback_smoke.py tests/test_summary_service.py tests/test_performance_smoke.py`

结果：**60 passed in 1.04s**，exit=0。

### 后端 M3 专属 focused 测试（新增，按要求纳入）

命令：`python3 -m pytest tests/test_m3_fact_alignment.py tests/test_m3_holding_summary.py tests/test_m3_issue_focus.py tests/test_m3_source_highlights.py`

结果：**29 passed in 0.94s**，exit=0。

### 前端

- `vitest run`（分两批）：47/47 + 54/54 = **101 passed**，0 failed。
- `tsc -b`：exit=0。
- `vite build`：**107 modules transformed**，built in 3.20s，exit=0。

> 环境说明（非阻断）：后端 `.venv` 为 Windows 侧、Linux VM 不可直用，本轮按既有解法在 VM 内分步增量装依赖（pydantic 2.7.1 / fastapi 0.111.0 等，`DATABASE_URL=sqlite` 规避 psycopg2，chromadb 惰性 import 不触发）后真实运行。`vite build` 因 Windows 侧 dist 清理触发 EPERM，按既有解法输出到 /tmp 干净目录。

## 八、性能与回滚验证

- 搜索链路 warm P95：`test_performance_smoke.py` 断言 `warm_response_total_duration_ms.p95 == 820` 且 `p95_under_3s is True`，门禁以 warm P95 为准 → **820ms < 3s，通过**。
- M3 详情 / 对比能力均不阻断主结果：gate 字段 `degrade_preserves_main_results=true`、`compare_view_closeable_and_rollbackable=pass`、`main_results_survive_detail_or_compare_failure=true`。
- 关闭 / 降级路径：每个案例侧单元格无锚点即安全降级（detail_unavailable / detail_loading / module_degraded / missing_source_anchor / source_chunk_unavailable / no_anchored_content / no_flagged_risk）；对比视图可关闭、移动端降级为单案分段视图。
- `ENABLE_WEIGHTED_RERANK` 未默认开启；M3 feature flags（SUMMARY / EXPANDED_SEARCH / QUERY_REWRITE / VITE_ENABLE_EXPANDED_SEARCH）默认 false。
- `.env.example` 未把 weighted rerank 默认改为 true。

## 九、止损触发器复核

来源锚点覆盖不足、用户原始案情持久化、正文泄露、绝对覆盖话术、胜负概率 / 确定性结论、qrels/label/relevance 进运行时、query/case id 特判、weighted rerank 默认开启、M2-8 核心门禁回归、M3 能力不可回滚或主链路不可用——**全部未触发**。

## 十、可见验收点说明

本环境 host↔VM 网络桥不可达，原生浏览器无法访问 VM 内 localhost；前端可见验收点通过 vitest + jsdom 真实组件树覆盖（CaseCompareAcceptance / CitationCopyAcceptance / SearchPage 等共 101 测试全绿，console error=0）。此为既有非阻断说明。

## 十一、结论与下一步

三类门禁全部 GO，无止损触发，无回归。允许进入 M4。

- 下一步新会话标题：**类案检索助手 M4-1 工作流沉淀入口合同**。
