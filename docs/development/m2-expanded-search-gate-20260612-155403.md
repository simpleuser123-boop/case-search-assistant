# M2-5 扩展检索受控入口开发报告

生成时间：2026-06-12 15:54:03 +08:00

## 入场产物引用

| Step | 产物 | 结论 |
| --- | --- | --- |
| M2-1 | `docs/development/m2-entry-contract-20260612-100850.md` / `.json` | GO |
| M2-2 | `docs/development/m2-source-anchor-closure-20260612-104120.md` / `.json` | GO |
| M2-3 | `docs/development/m2-coverage-privacy-20260612-140115.md` / `.json` | GO |
| M2-4 | `docs/development/m2-low-confidence-candidates-20260612-151624.md` / `.json` | GO |

入场门禁结论：GO。M2-1 至 M2-4 产物均存在且结论为 GO，因此允许执行 M2-5。

## Feature Flag 复核

| Flag | 配置位置 | 当前默认值 | 结论 |
| --- | --- | --- | --- |
| `ENABLE_EXPANDED_SEARCH` | `apps/api/app/core/config.py` | false | 安全关闭 |
| `ENABLE_EXPANDED_SEARCH` | `.env.example` | false | 安全关闭 |
| `VITE_ENABLE_EXPANDED_SEARCH` | `.env.example` | false | 安全关闭 |
| `ENABLE_WEIGHTED_RERANK` | `apps/api/app/core/config.py` | false | 保持关闭 |
| `ENABLE_WEIGHTED_RERANK` | `.env.example` | false | 保持关闭 |

本步骤未把任何默认关闭的检索或重排 flag 改为 true。

## 扩展检索触发条件

| 条件 | 实现状态 |
| --- | --- |
| 用户点击入口 | 前端 `handleExpandSearch` 主动调用 `/api/search/expand` |
| 主结果少于 5 条 | flag 开启时显示“扩大复核范围”入口 |
| 无结果 | flag 开启时显示“扩大复核范围”入口和修改描述建议 |
| flag 关闭 | 前端隐藏入口；后端返回 `EXPANDED_SEARCH_DISABLED` 且不执行检索 |

## 扩展检索策略

- 后端扩展入口只在 `/api/search/expand` 且 `ENABLE_EXPANDED_SEARCH` 处于开启态时执行。
- 扩展入口调用检索服务时传入 `include_relaxed_recall=true`。
- 标准入口 `/api/search` 继续传入 `include_relaxed_recall=false`。
- 扩展响应通过 `coverage.search_mode=expanded` 标记。
- 标准响应继续为 `coverage.search_mode=standard`。
- 低置信度候选继续复用 M2-4 的独立 `low_confidence_candidates` 字段，不覆盖 `results` 主排序语义。
- 来源锚点和隐私边界继续复用 M2-2、M2-3 的 API 过滤、日志脱敏和前端展示规则。

## 标准检索不受影响的证据

新增 focused test 验证同一测试服务中依次调用：

1. `/api/search`
2. `/api/search/expand`
3. `/api/search`

观测结果：

| 入口 | `include_relaxed_recall` | `coverage.search_mode` | 主结果语义 |
| --- | --- | --- | --- |
| 标准入口 before | false | standard | 仅返回标准主结果 |
| 扩展入口 | true | expanded | 补充候选进入独立候选区域 |
| 标准入口 after | false | standard | 仍仅返回标准主结果 |

结论：扩展检索不会覆盖标准检索默认入口，也不会改写 `results` 主排序语义。

## 前端入口和文案边界

修改了可见入口和状态文案：

| 状态 | 文案边界 |
| --- | --- |
| 低结果入口 | 使用“扩大复核范围” |
| 候选面板 | 使用“补充候选”“部分相关，仅供复核” |
| 加载状态 | 使用“补充候选加载中” |
| 失败状态 | 保留主结果，提示补充候选暂时不可用 |
| expanded 模式展示 | 使用“扩大复核范围” |

运行时代码扫描结果：未发现绝对召回承诺类文案。

## 回滚路径

| 层级 | 回滚方式 | 结果 |
| --- | --- | --- |
| 后端 | `ENABLE_EXPANDED_SEARCH=false` | `/api/search/expand` 返回 `EXPANDED_SEARCH_DISABLED`，不执行检索 |
| 前端 | `VITE_ENABLE_EXPANDED_SEARCH=false` | 不显示“扩大复核范围”入口，不触发扩展请求 |
| 重排 | `ENABLE_WEIGHTED_RERANK=false` | 继续使用标准基础排序或既有关闭态 |

## 测试和验证结果

| 命令 | 结果 |
| --- | --- |
| `cd apps/api; pytest tests/test_m2_expanded_search_gate.py tests/test_feature_flag_rollback.py` | 8 passed |
| `cd apps/web; npm run test -- SearchPage.test.tsx` | 27 passed |
| `cd apps/api; pytest tests/test_feature_flag_rollback.py tests/test_search_api_fallback_smoke.py tests/test_performance_smoke.py tests/test_health.py tests/test_m2_expanded_search_gate.py` | 24 passed |
| `cd apps/web; npm run test` | 42 passed |
| `cd apps/web; npm run build` | passed |

浏览器验收：

| 场景 | 结果 |
| --- | --- |
| 默认前端 flag 关闭 | 低结果测试数据下未显示扩展入口 |
| 前端 flag 开启 | 低结果测试数据下显示“扩大复核范围”入口 |
| 点击入口后 | 主结果保留，补充候选列表展示正常 |
| 控制台非阻断项 | favicon 缺失和前端-only dev server 下 `/api/events` 不可用 |

## 性能影响说明

- 标准入口没有新增扩展检索调用，默认链路不增加额外检索请求。
- 扩展检索只在用户点击或低/空结果入口显式触发后调用。
- `tests/test_performance_smoke.py` 已纳入本步骤完整后端验证命令并通过。
- 未观察到主结果白屏或主结果被补充候选覆盖。

## 正文泄露检查结论

- 后端 focused tests 覆盖 disabled 和 expanded 路径日志不记录原始输入或候选正文 sentinel。
- 正式 Markdown 和 JSON 产物只记录路径、count、mode、reason code、feature flag 状态、测试结果和结论。
- 浏览器验收产生的临时快照和 Vite 临时日志已删除，避免留下正文型测试内容。
- 本步骤未修改 qrels、label、历史评测结果。
- 本步骤未使用 query id、case id、qrels、relevance、label 做排序或扩展检索特判。

## 修改文件

- `apps/api/tests/test_m2_expanded_search_gate.py`
- `apps/web/src/components/results/LowConfidencePanel.tsx`
- `apps/web/src/components/feedback/EmptyResults.tsx`
- `apps/web/src/components/results/ResultOverview.tsx`
- `apps/web/src/services/searchApi.ts`
- `apps/web/src/pages/SearchPage.test.tsx`
- `docs/development/m2-expanded-search-gate-20260612-155403.md`
- `docs/development/m2-expanded-search-gate-20260612-155403.json`

## Go / No-Go 结论

GO。

理由：

- M2-1 至 M2-4 入场门禁均为 GO。
- flag 关闭态和开启态均有 focused tests。
- 标准检索默认行为未被扩展检索覆盖。
- 前端低结果和无结果入口符合受控触发条件。
- 文案仅表达扩大复核范围和补充候选，不表达绝对召回承诺。
- 性能 smoke 通过。
- `ENABLE_WEIGHTED_RERANK=false` 继续成立。
- 开发报告和 JSON 产物无正文泄露。

下一步仅在新会话中进入：

```text
类案检索助手 M2-6 相关不相关反馈闭环
```
