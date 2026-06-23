# M2-8 M2 验收与 M3 入口结论

生成时间：2026-06-12 19:19:45 +08:00

## M1.3x-10 复核结果

| 项 | 结果 | 证据 |
| --- | --- | --- |
| selected candidate | GO | `m1_3x_legal_score_shape_router_v2_candidate` |
| robustness | GO | `ROBUST_GO` |
| before->after REGRESSED | GO | `0` |
| after-vs-baseline REGRESSED | GO | `0` |
| METRIC_REGRESSION | GO | `0` |
| RECALL_MISS | GO | `7`，已作为剩余召回边界记录，非 M2 阻断项 |
| ENABLE_WEIGHTED_RERANK | GO | `apps/api/app/core/config.py=false`；`.env.example=false` |

结论：M1.3x-10 入场证据仍可作为 M2 验收基础。

## M2-1 至 M2-7 产物引用和结论汇总

| 步骤 | 产物 | 结论 |
| --- | --- | --- |
| M2-1 | `docs/development/m2-entry-contract-20260612-100850.md` / `.json` | GO |
| M2-2 | `docs/development/m2-source-anchor-closure-20260612-104120.md` / `.json` | GO |
| M2-3 | `docs/development/m2-coverage-privacy-20260612-140115.md` / `.json` | GO |
| M2-4 | `docs/development/m2-low-confidence-candidates-20260612-151624.md` / `.json` | GO |
| M2-5 | `docs/development/m2-expanded-search-gate-20260612-155403.md` / `.json` | GO |
| M2-6 | `docs/development/m2-feedback-loop-20260612-171213.md` / `.json` | GO |
| M2-7 | `docs/development/m2-risk-hints-20260612-181106.md` / `.json` | GO |

前置门禁：全部存在且全部为 GO，因此允许执行 M2-8 总验收。

## 来源锚点验收

| 门禁 | 结果 | 说明 |
| --- | --- | --- |
| 用户可见 AI 加工内容有来源锚点 | GO | M2-2、M2-7 均要求无锚点内容隐藏或降级 |
| source_anchors 可追溯 case_id 与 source_chunk_id | GO | M2-2 合同和 focused tests 已覆盖 |
| 无来源 AI 内容处理 | GO | `tests/test_m2_source_anchor_closure.py`、`tests/test_m2_risk_hints.py` 通过 |
| 详情与结果页来源入口 | GO | 浏览器验收中来源入口可见 |

## 数据覆盖与隐私验收

| 门禁 | 结果 | 说明 |
| --- | --- | --- |
| coverage 可展示 | GO | 浏览器验收中数据来源、候选规模、索引版本可见 |
| 不编造不可用字段 | GO | M2-3 JSON 记录不可用字段降级策略 |
| 用户输入不持久化 | GO | M2-3、M2-6 隐私检查通过 |
| 只保存脱敏字段 | GO | feedback 和 analytics focused tests 通过 |

## 低置信度候选验收

| 门禁 | 结果 | 说明 |
| --- | --- | --- |
| 主结果和补充候选字段分离 | GO | M2-4 API 字段合同通过 |
| 主结果和补充候选视觉分离 | GO | 浏览器验收中补充候选区块和候选列表可见 |
| 分层不依赖离线评测字段或 ID 特判 | GO | `tests/test_m2_low_confidence_candidates.py` 通过 |

## 扩展检索验收

| 门禁 | 结果 | 说明 |
| --- | --- | --- |
| 后端默认关闭 | GO | `ENABLE_EXPANDED_SEARCH=false` |
| 前端默认关闭 | GO | `VITE_ENABLE_EXPANDED_SEARCH=false` |
| 可受控打开并回滚 | GO | M2-5 产物和 feature flag rollback tests 通过 |
| 不覆盖标准检索默认行为 | GO | M2-5 标准检索证据和 fallback smoke 通过 |
| 不导致主结果白屏 | GO | 浏览器验收保留主结果并显示补充候选 |

## 反馈事件验收

| 门禁 | 结果 | 说明 |
| --- | --- | --- |
| 反馈只保存脱敏字段 | GO | `tests/test_m2_feedback_loop.py` 与前端 feedback tests 通过 |
| 反馈不影响排序 | GO | M2-6 产物记录排序边界，focused tests 通过 |
| 前端反馈控件可见 | GO | 浏览器验收中相关/不相关控件可见并可点击 |

## 风险提示验收

| 门禁 | 结果 | 说明 |
| --- | --- | --- |
| risk_hints 全部有来源锚点 | GO | M2-7 focused tests 通过 |
| 只表达复核线索 | GO | 运行时代码禁用确定性承诺，前端文案为复核提示 |
| 不影响主排序 | GO | M2-7 产物和 tests 通过 |
| 不使用离线评测字段或 ID 特判 | GO | `tests/test_m2_risk_hints.py` 通过 |

## 禁用文案扫描

| 扫描范围 | 结果 | 说明 |
| --- | --- | --- |
| `apps/api/app` | GO | 运行时代码 0 命中绝对覆盖承诺或诉讼结果承诺 |
| `apps/web/src` | GO | 用户可见前端代码 0 命中绝对覆盖承诺或诉讼结果承诺 |
| tests | GO | 仅存在负向断言，验证相关承诺不会进入输出 |

## 正文泄露扫描

| 扫描范围 | 结果 | 说明 |
| --- | --- | --- |
| M2 JSON 产物 | GO | 仅保存字段名、count、状态、reason code、flag 状态、指标摘要和测试结果 |
| M2 Markdown 产物 | GO | 未写入案情、候选、chunk 或裁判文书正文 |
| 运行时代码日志边界 | GO | 日志使用 hash、count、duration、reason code，不记录正文型内容 |
| 临时浏览器日志 | GO | M2-8 临时日志已删除，未保留到开发目录 |
| sentinel 扫描 | GO | 开发产物和运行时代码未命中正文 sentinel；测试文件中的 sentinel 仅用于负向断言 |

## 性能和回滚验证

| 门禁 | 结果 | 说明 |
| --- | --- | --- |
| warm P95 | GO | `tests/test_performance_smoke.py` 覆盖 warm P95，测试断言 warm P95 为 820ms |
| 主搜索链路 | GO | health、fallback、candidate robustness、summary、performance tests 均通过 |
| 扩展检索回滚 | GO | `tests/test_feature_flag_rollback.py` 与 `tests/test_m2_expanded_search_gate.py` 通过 |
| ENABLE_WEIGHTED_RERANK 默认关闭 | GO | config 与 `.env.example` 均为 false |
| 扩展检索默认关闭 | GO | 后端与前端示例默认均为 false |

## 验证命令和结果

| 命令 | 结果 |
| --- | --- |
| `cd apps/api; pytest tests/test_m1_3_candidate_comparison.py tests/test_m1_3_legal_candidate_robustness.py tests/test_feature_flag_rollback.py tests/test_health.py tests/test_search_api_fallback_smoke.py tests/test_summary_service.py tests/test_performance_smoke.py tests/test_m2_entry_contract.py tests/test_m2_source_anchor_closure.py tests/test_m2_low_confidence_candidates.py tests/test_m2_expanded_search_gate.py tests/test_m2_feedback_loop.py tests/test_m2_risk_hints.py` | 95 passed |
| `cd apps/web; npm run test` | 5 files passed，48 tests passed |
| `cd apps/web; npm run build` | passed |
| 浏览器验收 | passed；数据覆盖、来源入口、风险提示、补充候选、反馈控件均可见；console error count 0 |

## 基础搜索 GO/NO_GO

GO。

依据：health、fallback、candidate robustness、summary、performance、rollback 均通过；M1.3x-10 核心门禁未回归。

## M2 可信检索 GO/NO_GO

GO。

依据：来源锚点、coverage、低置信度候选、扩展检索、反馈闭环、风险提示均通过；未发现阻断隐私、文案、来源或默认开关问题。

## M3 入口 GO/NO_GO

GO。

允许进入下一阶段，但本轮未实现 M3 阅读提效能力。

下一步新会话标题：

```text
类案检索助手 M3-1 阅读提效入口合同
```

## NO_GO 失败项

无。

| 项 | 结果 |
| --- | --- |
| 失败步骤 | 无 |
| 失败门禁 | 无 |
| 受影响能力 | 无 |
| 是否需要回滚 | 否 |
| 是否阻止进入 M3 | 否 |
| 下一轮建议 | 进入 M3-1 |
