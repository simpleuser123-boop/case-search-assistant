# M2-4 低置信度候选分层开发报告

生成时间：2026-06-12 15:16:24

## 入场产物引用

| Step | 产物 | 结论 |
| --- | --- | --- |
| M2-1 | `docs/development/m2-entry-contract-20260612-100850.md` / `.json` | GO |
| M2-2 | `docs/development/m2-source-anchor-closure-20260612-104120.md` / `.json` | GO |
| M2-3 | `docs/development/m2-coverage-privacy-20260612-140115.md` / `.json` | GO |

入场门禁结论：GO。三个前置产物均存在且结论为 GO。

## 分层合同

低置信度候选只作为复核辅助，不进入主结果排序合同：

- `results`：主结果，保持现有主排序语义，只从已排序候选中保留非低置信度项。
- `low_confidence_candidates`：低置信度候选，独立数组返回，不挤占或改写 `results`。
- `confidence_level`：`high`、`medium`、`low`。
- `confidence_reasons`：脱敏 reason code 数组，不包含案情正文、候选正文或 chunk 正文。
- `confidence_score_band`：分数区间，只返回区间码。
- `original_rank`：进入分层前的排序位置，用于证明分层不改写原始排序。

低置信度候选进入条件：

| 条件 | reason code | 运行时特征 |
| --- | --- | --- |
| 最终分数低于低置信度上界 | `LOW_SCORE_BAND` | final score 区间 |
| 来自宽松召回来源 | `RELAXED_RECALL_SOURCE` | retrieval source |
| 法律要素命中数量不足且分数低 | `LOW_LEGAL_ELEMENT_HIT_COUNT` | legal element hit count |
| 降级链路下来自回退召回且分数低 | `DEGRADED_SEARCH_PATH` | degraded reason + retrieval source + score |
| 主结果不足目标数量时展示低置信度候选 | `MAIN_RESULT_COUNT_BELOW_TARGET` | main result count |

分数区间合同：

- `0.00-0.65`：低置信度分数区间。
- `0.65-0.78`：中置信度分数区间。
- `0.78-1.00`：高置信度分数区间。

数量边界：

- 主结果目标数量：5。
- 低置信度候选最多返回：5。

## 非标签特征证据

本步骤仅使用运行时非标签特征：

- final score 区间。
- retrieval source。
- legal element hit count。
- main result count。
- degraded reason code。

明确未使用以下输入：

- qrels。
- relevance。
- label。
- query id。
- case id。
- 人工 rank 修正。
- 历史评测结果。

代码证据：

- `apps/api/app/retrieval/confidence.py` 的分层函数只接收已排序候选、limit 和降级 reason code。
- `apps/api/app/rerank/service.py` 仅把法律要素命中数量写入 `score_breakdown`，并排除评测标签类 metadata。
- `apps/api/app/api/search.py` 在 rerank 后调用分层函数，未把 qrels、relevance、label、query id 或 case id 传入分层函数。
- `apps/api/tests/test_m2_low_confidence_candidates.py` 覆盖禁用字段不进入运行时 router、标签字段不参与法律要素命中、query id / case id 不影响分层签名。

## API 字段变化

`apps/api/app/schemas.py`：

- `SearchResponse.low_confidence_candidates`：新增独立低置信度候选数组。
- `SearchResultItem.confidence_level`：新增候选置信度层级。
- `SearchResultItem.confidence_reasons`：新增脱敏原因码数组。
- `SearchResultItem.confidence_score_band`：新增分数区间。
- `SearchResultItem.original_rank`：新增分层前排序位置。

兼容策略：

- `SearchResponse.results` 保留主结果语义。
- `SearchResponse.candidates` 继续指向主结果，维持旧前端兼容。
- 低置信度候选不进入 `results`。

## 前端展示策略

`apps/web/src/components/results/LowConfidencePanel.tsx` 新增独立低置信度候选区域：

- 主结果继续由主结果列表展示，保持主视觉权重。
- 低置信度候选使用独立面板，标题为“部分相关，仅供复核”。
- 低置信度候选卡片使用 `variant="lowConfidence"`。
- 标准搜索响应中的 `low_confidence_candidates` 优先展示；扩展检索旧兼容路径保持在 feature flag 控制下。

## 文案边界

已采用克制表达：

- “可能相关候选”
- “部分相关，仅供复核”
- “候选不替代主结果排序”

未使用绝对承诺话术：

- 未使用完成式查全承诺。
- 未使用遗漏保证承诺。
- 未使用覆盖率承诺。
- 未把低置信度候选包装成查全结果。

## 测试和验证结果

已运行验证：

| 命令 | 结果 |
| --- | --- |
| `cd apps/api; pytest tests/test_m2_low_confidence_candidates.py` | 5 passed |
| `cd apps/web; npm run test -- SearchPage.test.tsx` | 27 passed |
| `cd apps/api; pytest tests/test_m1_3_candidate_comparison.py tests/test_m1_3_legal_candidate_robustness.py tests/test_feature_flag_rollback.py tests/test_health.py tests/test_m2_low_confidence_candidates.py` | 46 passed |
| `cd apps/web; npm run test` | 42 passed |
| `cd apps/web; npm run build` | passed |

Focused tests 覆盖：

- qrels 不传入 runtime router。
- relevance、label 不参与运行时分层。
- query id / case id 不影响分层结果。
- 低置信度候选不挤占主结果排序。
- API 字段 `results` 和 `low_confidence_candidates` 分离。
- 前端能区分主结果和低置信度候选。
- 文案不承诺查全。
- 报告、日志、JSON 产物不输出正文。

## 配置和数据边界

- `ENABLE_WEIGHTED_RERANK=false` 继续成立。
- 未修改 qrels、label 或历史评测结果。
- 未改变 source selection。
- 未开启 weighted rerank。
- 未使用 query id / case id 特判。
- 未手工改 rank。

## 正文泄露检查结论

开发报告、JSON 产物、日志字段和 focused tests 只保留：

- 字段名。
- count。
- rank。
- score 区间。
- reason code。
- feature flag 状态。
- 测试结果。
- GO/NO_GO 结论。

未输出原始 query、案情正文、候选正文或 chunk 正文。

## Go / No-Go 结论

GO。

理由：

- M2-1、M2-2、M2-3 入场产物均为 GO。
- 主结果和低置信度候选字段已分离。
- 分层只依赖运行时非标签特征。
- 低置信度候选不挤占主结果排序。
- 前端文案不承诺查全。
- qrels、relevance、label 未进入运行时分层。
- query id / case id 未用于特判。
- M1.3x-10 核心门禁验证通过。
- `ENABLE_WEIGHTED_RERANK=false` 继续成立。
- 开发报告和 JSON 产物无正文泄露。

下一步仅在新会话中进入：类案检索助手 M2-5 扩展检索受控入口。
