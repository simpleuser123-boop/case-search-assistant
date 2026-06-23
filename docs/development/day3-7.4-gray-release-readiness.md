# Day 3 7.4 灰度发布准备记录

日期：2026-06-08

范围：仅完成本机/内部灰度准备。不真实邀请律师，不部署公网生产，不修改语料、索引、排序策略或权重，不做回滚演练。

## 1. 证据口径

| 来源 | 当前证据 | 对 7.4 的影响 |
| --- | --- | --- |
| 7.1 端到端联调 | 仓库存在 `apps/web/scripts/day3-7.1-e2e-smoke.mjs`，覆盖首页提交、结果、详情、重搜、扩展检索、异常和埋点隐私；`docs/development` 未发现持久化 7.1 运行记录。 | 不作为外部灰度放量依据；本轮只准备内部检查清单。 |
| 7.2 离线评测 | `data/eval/day3_rerank_eval_20260608.json`：BM25 baseline `Precision@5=0.4987`、`NDCG@10=0.4442`；当前 rerank 对 qrels 评测因候选语料不可用/数据集不匹配被阻塞；`release_decision.enable_new_rerank=false`。 | 评测结论属于模糊/阻塞，不启用新排序，只保留日志和评测管道。 |
| 7.3 性能与稳定性 | 仓库存在 `apps/api/scripts/day3_7_3_performance_smoke.py`，但未发现持久化性能结果文件。 | 灰度前不得把摘要、查询改写或扩展检索默认打开；外部灰度前需先补跑性能 smoke。 |

## 2. 灰度配置表

| Feature flag | 默认值 | 本机/内部 7.4 建议 | 关闭后行为 | 启用前置条件 |
| --- | --- | --- | --- | --- |
| `ENABLE_QUERY_REWRITE` | `false` | 保持关闭 | 原始输入直接进入检索，响应标记 `QUERY_REWRITE_DISABLED` | 需由 7.3 证明改写 P95 可接受，且 LLM 超时率可控。 |
| `ENABLE_WEIGHTED_RERANK` | `false` | 保持关闭 | 回到基线检索分排序，`score_mode=base_retrieval` | 需由 7.2 证明 Precision@5 不下降、NDCG@10 正向、Top10 主观命中率接近或达到 60%。 |
| `ENABLE_SUMMARY` | `false` | 保持关闭 | 展示来源片段/抽取式摘要，不请求 LLM 摘要 | 需由 7.3 证明摘要不会拖慢 P95；摘要必须绑定 `case_id` 和 `source_chunk_id`。 |
| `ENABLE_EXPANDED_SEARCH` | `false` | 保持关闭 | `/api/search/expand` 返回 `EXPANDED_SEARCH_DISABLED`；前端默认隐藏扩展入口 | 需由 7.1/7.3 确认扩展检索不会造成白屏或不可控延迟。 |
| `VITE_ENABLE_EXPANDED_SEARCH` | `false` | 与 `ENABLE_EXPANDED_SEARCH` 同步 | 前端隐藏扩展检索入口 | 仅用于 Web 入口展示，不能替代后端 `ENABLE_EXPANDED_SEARCH`。 |

`.env.example` 只保留示例和空密钥占位，不包含真实 key。

## 3. 监控指标清单

| 指标 | 观测来源 | 隐私边界 |
| --- | --- | --- |
| 搜索完成率 | `search_submit` 与 `search_result_render` 事件、服务端 `search_completed` 日志 | 只使用 `query_session_id`、`input_length`、`result_count`。 |
| 错误率 | API HTTP 状态码、统一错误 `code`、前端 `SearchApiError` | 不记录原始 query、案情全文或密钥。 |
| `total_duration_ms` | `/api/search.timings.total_duration_ms`、`search_result_render.metadata.total_duration_ms` | 仅耗时数字。 |
| 点击率 | `result_card_click` / 搜索完成会话数 | 使用 `case_id_hash`，不上传原始 `case_id`。 |
| 二次搜索率 | `search_refine` / 搜索完成会话数 | 只记录 `refine_count`、`previous_result_count`、`input_length`。 |
| 无结果率 | `search_zero_result` / 搜索完成会话数 | 只记录 `input_length`、`fallback_available`。 |
| 降级触发次数 | `/api/search.degraded_reasons`、服务端日志、`search_result_render.metadata.degraded_reason_count` | 记录原因枚举和次数，不记录原始案情。 |

事件 payload 禁止包含：`query`、`raw_query`、`raw_text`、`content`、`text`、`case_text`、`fact`、`prompt`、密钥、手机号、身份证号或裁判文书长文本。

## 4. 灰度决策

当前判定：仅允许本机/内部配置演练，不允许对律师外部灰度。

原因：

- 7.2 已明确 `enable_new_rerank=false`，新排序缺少可对齐 qrels 的正向指标。
- 7.3 没有持久化 P95/P99 结果，不能证明摘要、查询改写或扩展检索在内测负载下稳定。
- 第 9 节硬止损线要求 Day 3 上线前必须完成回滚演练；7.5 尚未执行，因此不能灰度上线。

新排序启用规则：

- 评测正向：才可以灰度启用 `ENABLE_WEIGHTED_RERANK=true`。
- 评测模糊：只启用日志和评测管道，`ENABLE_WEIGHTED_RERANK=false`。
- 评测负向：保留代码，所有增强 flag 关闭。

本轮结论属于评测模糊/阻塞：不启用新排序。

## 5. 内部检查清单

1. `cd apps/api; pytest` 必须通过。
2. `cd apps/web; npm test` 必须通过。
3. `cd apps/web; npm run build` 必须通过。
4. `GET /health` 必须能看到四个 `feature_flags` 且不泄露密钥值。
5. 前端事件仍不得包含原始案情文本；隐私测试失败即停止。
6. 若要做本机开关演练，只能在本机临时环境变量中切换，不修改语料/索引：
   - 关闭态：`ENABLE_WEIGHTED_RERANK=false`，验证 `/api/search` 的 `score_breakdown.score_mode=base_retrieval`。
   - 开启态：仅在评测正向后临时设 `ENABLE_WEIGHTED_RERANK=true`，验证 `score_mode=weighted_rerank`。
   - 扩展检索关闭态：`ENABLE_EXPANDED_SEARCH=false`，验证 `/api/search/expand` 返回 `EXPANDED_SEARCH_DISABLED`，前端不显示扩展入口。
7. 外部灰度前必须先完成 7.5 回滚演练并记录恢复时间。

## 6. 7.5 准入

允许进入 7.5：是，但仅限回滚演练。

不允许执行：对外发布、邀请律师、部署公网生产、打开新排序灰度。

下一步标题：类案检索助手 Day3-7.5 回滚演练
