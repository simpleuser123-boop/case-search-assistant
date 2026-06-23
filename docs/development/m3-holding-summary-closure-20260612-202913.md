# M3-2 裁判要旨摘要来源闭环验收报告

生成时间：2026-06-12 20:29:13

## 结论

M3-2 结论：GO。

本步骤仅实现案例详情页的裁判要旨摘要来源闭环，不进入 M3-3 及后续能力。基础搜索排序、候选召回、source selection、rerank 默认开关、M2 扩展检索默认行为均未变更。`ENABLE_WEIGHTED_RERANK` 保持 `false`。

## 前置门禁

- M3-1：`docs/development/m3-reading-entry-contract-20260612-194511.json`，结论 GO，允许进入 M3-2。
- M2-2：`docs/development/m2-source-anchor-closure-20260612-104120.json`，结论 GO。
- M2-3：`docs/development/m2-coverage-privacy-20260612-140115.json`，结论 GO。
- 加权重排默认值：`apps/api/app/core/config.py` 与 `.env.example` 均为 `false`。

## 实现范围

- 在案例详情返回结构中新增 `holding_summary`。
- `holding_summary` 最小结构包含：
  - `summary_items`
  - `source_anchors`
  - `confidence`
  - `generation_status`
  - `degrade_reason`
- 每条可见裁判要旨摘要项必须有真实 `source_anchors`。
- 每个锚点至少包含 `case_id` 与 `source_chunk_id`。
- 锚点只来自详情页已有来源片段链路，不手工绑定假锚点。
- 无锚点、来源不一致、片段不足、模型失败时，详情页隐藏 AI 摘要或安全降级。

## 降级 reason code

- `missing_source_anchor`
- `insufficient_source`
- `model_failed`
- `source_mismatch`

日志、报告、JSON、测试快照只允许记录以上脱敏 reason code、状态与计数，不记录正文内容。

## 前端展示规则

- 详情页显示“裁判要旨摘要”时，同步显示来源入口或可点击锚点。
- 前端会校验摘要项锚点是否能对应当前详情页返回的来源片段。
- 未通过锚点校验的摘要项不展示。
- 文案限定为阅读辅助、复核线索、来源定位，不表达胜诉败诉、风险定级、结果概率或确定性法律结论。
- 降级状态不影响详情页基础信息和来源入口展示。

## 修改文件

- `apps/api/app/schemas.py`
- `apps/api/app/summary/service.py`
- `apps/api/app/summary/__init__.py`
- `apps/api/app/api/cases.py`
- `apps/api/tests/test_m3_holding_summary.py`
- `apps/web/src/types/search.ts`
- `apps/web/src/components/details/CaseDetailDrawer.tsx`
- `apps/web/src/pages/SearchPage.test.tsx`

## 验证结果

- `cd apps/api; pytest tests/test_m3_holding_summary.py tests/test_summary_service.py`
  - 结果：16 passed
- `cd apps/api; pytest tests/test_summary_service.py tests/test_search_api_fallback_smoke.py tests/test_feature_flag_rollback.py tests/test_performance_smoke.py tests/test_m3_holding_summary.py`
  - 结果：28 passed
- `cd apps/web; npm run test`
  - 结果：5 passed files，49 passed tests
- `cd apps/web; npm run build`
  - 结果：passed

## 浏览器验收

- 本地 API 与 Web 服务启动成功。
- 首页打开不白屏。
- 基础搜索页打开不白屏。
- 案例详情抽屉打开不白屏。
- 有锚点的裁判要旨摘要展示来源入口。
- 无锚点和模型失败降级路径已由 focused tests 覆盖。

## 泄露检查

本报告和配套 JSON 不包含原始 query、案情正文、候选正文、chunk 正文、裁判文书正文、摘要正文或来源片段正文。

## 下一步

若继续推进，下一步新会话标题：

`类案检索助手 M3-3 争议焦点与关键要素提炼`
