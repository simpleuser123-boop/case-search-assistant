# M2-3 数据覆盖声明与隐私展示

生成时间：2026-06-12 14:01:15 +08:00

## 入场门禁

- M2-1 入场产物：
  - `docs/development/m2-entry-contract-20260612-100850.md`
  - `docs/development/m2-entry-contract-20260612-100850.json`
  - 结论：GO
- M2-2 入场产物：
  - `docs/development/m2-source-anchor-closure-20260612-104120.md`
  - `docs/development/m2-source-anchor-closure-20260612-104120.json`
  - 结论：GO
- `ENABLE_WEIGHTED_RERANK`：
  - `apps/api/app/core/config.py` 默认值：false
  - `.env.example`：false
- 本步骤未修改 qrels、label 或历史评测结果。

## coverage 字段合同

`SearchResponse.coverage` 新增字段：

| 字段 | 类型 | 含义 | 不可用状态 |
| --- | --- | --- | --- |
| `data_source` | string | 本次返回候选中可一致确认的数据来源字段 | `unavailable` |
| `data_until` | string | 本次返回候选中可一致确认的数据截止字段 | `unknown` |
| `index_version` | string | 本次实际使用且未降级的向量集合标识 | `unknown` |
| `total_candidate_count` | integer/null | 本次检索合并后的运行时候选数量 | `null` |
| `search_mode` | `standard`/`expanded` | 本次入口模式 | `standard` |
| `degraded_reasons` | string[] | 本次降级和 coverage 空状态 reason code | `[]` |

该合同只描述数据和策略边界，不表达穷尽承诺。

## 可信来源说明

- `total_candidate_count`：来自后端本次检索合并后的 `case_candidates` 长度，表示运行时候选规模，不使用外部总库量。
- `search_mode`：来自当前 API 入口，标准入口为 `standard`，扩展入口为 `expanded`。
- `degraded_reasons`：合并检索链路既有降级 reason code 与 coverage 空状态 reason code。
- `data_source`：仅当所有候选 metadata 的 `source_name` 或 `data_source` 能形成唯一非空值时返回具体值；否则返回 `unavailable`。
- `data_until`：仅当候选 metadata 明确提供 `data_until`、`coverage_until`、`source_data_until` 或 `index_data_until` 且形成唯一非空值时返回具体值；不从裁判日期推断。
- `index_version`：仅当本次使用向量来源且未出现 Chroma 降级 reason code 时返回 `CHROMA_COLLECTION`；BM25 fallback、Chroma 降级或无向量来源时返回 `unknown`。

新增 coverage 空状态 reason code：

- `DATA_SOURCE_UNAVAILABLE`
- `DATA_UNTIL_UNKNOWN`
- `INDEX_VERSION_UNKNOWN`

## 不可用字段降级策略

- 数据来源无法可信确认：`data_source=unavailable`，追加 `DATA_SOURCE_UNAVAILABLE`。
- 数据截止日期无法可信确认：`data_until=unknown`，追加 `DATA_UNTIL_UNKNOWN`。
- 索引版本无法可信确认：`index_version=unknown`，追加 `INDEX_VERSION_UNKNOWN`。
- 候选数量只使用本次运行时合并候选数；未引入未证实总量。
- 降级文案只描述事实状态，例如基础检索、索引信息不可用、增强能力不可用。

## 前端展示位置和文案边界

- 结果页概览区展示：总耗时、候选规模、数据来源、数据截止、检索模式、索引版本。
- coverage 字段不可用时展示克制空状态：当前数据覆盖信息暂不可用，按本次可用检索结果展示。
- 降级时展示事实性标题：已使用基础检索。
- 首页和结果页顶部移除硬编码数据截止日期，改为以本次 API 返回为准。
- 详情页保留 M2-2 已建立的来源锚点、来源段落和原文链接入口；本步骤未改变 source selection。
- 文案边界：不声明穷尽、不声明保证、不使用绝对覆盖指标表达。

## 隐私边界和正文泄露检查

- 后端搜索日志记录 `query_session_id`、`input_hash`、候选数量、结果数量、降级 reason code 和耗时，不记录原始 query。
- 后端 focused smoke 覆盖：原始 query sentinel 和 chunk body sentinel 不进入日志。
- 埋点接口拒绝敏感 metadata key，只记录 metadata key 列表与脱敏字段。
- 前端 analytics 仅发送 allowlist 字段：输入长度、触发方式、是否恢复草稿等；不发送原始 query。
- 前端移除首页输入草稿的 `localStorage` 持久化；当前输入仅存在于 React 状态和提交后的路由 state 生命周期内，刷新或离开页面即清除。
- 未发现原始案情、候选正文、chunk 正文进入本报告或 JSON 产物。

## 禁用文案扫描结果

扫描范围：

- `apps/web/src`
- `apps/api/app`
- `apps/api/tests`

结果：

| 扫描项 | 结果 |
| --- | --- |
| `ABSOLUTE_COPY_EXHAUSTIVE_1` | 通过 |
| `ABSOLUTE_COPY_EXHAUSTIVE_2` | 通过 |
| `ABSOLUTE_COPY_RECALL_RATE` | 通过 |
| 未证实的数据总量表达 | 通过 |
| 将覆盖说明包装为绝对穷尽 | 通过 |
| 旧硬编码数据截止日期 | 通过 |
| 前端持久化原始输入 key | 通过 |

## 测试和验证结果

- `cd apps/api; pytest tests/test_health.py tests/test_search_api_fallback_smoke.py tests/test_feature_flag_rollback.py`
  - 结果：通过，18 passed。
- `cd apps/api; pytest tests/test_day1_api_skeleton.py`
  - 结果：通过，22 passed。
  - 用途：额外验证搜索响应 schema 兼容性。
- `cd apps/web; npm run test`
  - 结果：通过，41 passed。
  - 中间失败记录：首次运行失败，原因 code 为 `TEST_LOCATOR_MULTIPLE_MATCHES`；修正测试定位后重跑通过。
- `cd apps/web; npm run build`
  - 结果：通过。

## Go/No-Go 结论

GO。

理由：

- M2-1 和 M2-2 最新入场产物均为 GO。
- coverage API 已返回字段合同要求的六项信息。
- 无法可信确认的数据来源、截止日期、索引版本使用安全空状态。
- 前端已展示 coverage 信息和降级事实说明。
- 禁用文案扫描通过。
- 原始案情不进入后端持久化日志、不进入埋点、不进入本步骤报告/JSON 产物。
- 前端不再把原始 query 写入可持久化 storage。
- `ENABLE_WEIGHTED_RERANK=false` 保持成立。
- 未修改排序权重、召回逻辑、source selection、qrels、label、历史评测结果。
