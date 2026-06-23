# M4-1 工作流沉淀入口合同

- 生成时间：2026-06-13T09:03:54+08:00
- 步骤：M4-1（工作流沉淀入口合同）
- 配套产物：`m4-workflow-entry-contract-20260613-090354.json`
- 范围：只冻结合同、定义字段白名单、写合同测试；**不实现** M4-2 及之后任何沉淀业务能力。

## 一、Go/No-Go 结论

| 结论项 | 判定 |
| --- | --- |
| M4-1 工作流沉淀入口合同 | **GO** |
| 允许进入 M4-2 | 是 |
| 是否需要回滚 | 否 |

下一步新会话标题：**类案检索助手 M4-2 检索历史与草稿恢复**。

## 二、M4 entry register（入场证据冻结）

入场依据为 M3-8 验收产物，三类结论与 M4 入口均为 GO：

- `docs/development/m3-final-verification-20260613-072734.md`
- `docs/development/m3-release-gate-20260613-072734.json`
- `docs/development/m3-parameter-version-20260613-072734.json`

| 结论项 | 状态 |
| --- | --- |
| 基础搜索继续可用 | GO |
| M3 阅读提效完成 | GO |
| M4 入口 | GO |

### M3-1 至 M3-7 最新产物与状态

| 步骤 | 能力 | 产物前缀 | 状态 |
| --- | --- | --- | --- |
| M3-1 | 阅读入口合同 | m3-reading-entry-contract-20260612-194511 | GO |
| M3-2 | 裁判要旨摘要 | m3-holding-summary-closure-20260612-202913 | GO |
| M3-3 | 争议焦点与关键要素 | m3-issue-focus-elements-20260612-212111 | GO |
| M3-4 | 相似事实对比 | m3-fact-alignment-20260613-021821 | GO |
| M3-5 | 相似片段高亮 | m3-source-highlights-20260613-035436 | GO |
| M3-6 | 案例对比视图 | m3-case-compare-gate-20260613-054541 | GO |
| M3-7 | 复制案号与引用格式 | m3-citation-copy-boundary-20260613-062814 | GO |

### M2/M3 仍需继承的门禁

- 来源锚点最小字段 `case_id` + `source_chunk_id`；案例侧 AI 内容必须有锚点，无锚点隐藏或安全降级。
- 用户原始案情不持久化到服务端；不输出"已查全/保证无遗漏/查全率"；不输出胜负概率或确定性法律结论。
- 不改主排序、source selection、rerank 默认开关；qrels/label/relevance 仅离线；不以 query id / case id 做运行时特判。
- 主搜索 warm P95 < 3s（M3-8 实测 820ms）；新能力必须可关闭或安全降级。

### ENABLE_WEIGHTED_RERANK=false 当前证据

- `apps/api/app/core/config.py`：`ENABLE_WEIGHTED_RERANK: bool = False`。
- `.env.example`：`ENABLE_WEIGHTED_RERANK=false`。
- M4-1 决策：`KEEP_FALSE`，不在本步或后续 M4 默认开启。

## 三、M4 工作流沉淀合同字段

合同共定义 7 类字段，详见 JSON `workflow_contract_fields`。M4-1 仅声明，`implemented_in_m4_1` 全部为 `false`，全部 `affects_ranking=false`、`logs_events_reports_may_store_body=false`。

| 字段 | feature flag | 关键边界 |
| --- | --- | --- |
| `search_history` | ENABLE_SEARCH_HISTORY | 服务端仅存 query_session_id/input_hash/result_count/降级状态/时间戳；原始 query 不上送 |
| `search_draft` | ENABLE_SEARCH_HISTORY | 草稿正文仅本地、可清除；服务端持久字段为空 |
| `case_favorite` | ENABLE_CASE_FAVORITE | 仅元数据 + 来源锚点 + 用户自填短字段；不存正文 |
| `case_list` | ENABLE_CASE_LIST | 仅引用 + 用户自填；同案去重；手动排序只影响展示 |
| `case_list_export` | ENABLE_LIST_EXPORT | 仅元数据/来源链接/备注；含免责说明；无正文/无胜负概率 |
| `report_template` | ENABLE_REPORT_TEMPLATE | 模板骨架 + 元数据 + 锚点；AI 片段必须有锚点；不起草、不下结论 |
| `team_reuse_capability` | ENABLE_TEAM_REUSE | 仅评估 + 预留字段(默认私有/关闭共享)；不实现团队空间 |

## 四、持久化字段白名单（M4 最关键产物）

后续 M4-2 至 M4-7 **只能**向持久层写入下列白名单字段；未声明字段一律不落库。

### 允许持久化

`case_id`、`case_number`、`court`、`trial_level`、`case_cause`、`judgment_date`、`source_anchors`、用户自填 `note`/`tag`/`label`、`list_id`/`list_title`/`report_id`/`export_id`、清单/收藏的结构化关系 `structured_relation`、时间戳 `created_at`/`updated_at`/`timestamp`、`status`、`reason_code`、`feature_flag_state`。

### 禁止持久化

原始 `raw_query`、案情正文 `case_fact_body`、自由文本长片段、候选正文 `candidate_body`、chunk 正文 `chunk_body`、裁判文书长正文 `judgment_long_text`、摘要/要旨/对比的正文型内容、用户输入自由长文本、未脱敏个人信息。

### 仅本地、可清除

用户原始案情和草稿正文只允许存在于**浏览器侧**（`draft_text_local_only`、`raw_case_fact_local_only`、`history_entries_local_only`），可一键清除；**不写入服务端持久层**（`user_raw_case_persisted_on_server=false`、`draft_body_persisted_on_server=false`）。

## 五、锚点继承规则

- 清单、导出、报告引用的案例侧 AI 内容必须可追溯 `case_id` + `source_chunk_id`。
- 无锚点内容不进入清单、导出或报告交付物（`no_anchor_no_delivery=true`）。
- 不伪造 `source_chunk_id`。

## 六、M4 feature flag 策略

- `ENABLE_WEIGHTED_RERANK=false` 继续保持。
- 新增 6 个可关闭开关，默认安全态 `false`：`ENABLE_SEARCH_HISTORY`、`ENABLE_CASE_FAVORITE`、`ENABLE_CASE_LIST`、`ENABLE_LIST_EXPORT`、`ENABLE_REPORT_TEMPLATE`、`ENABLE_TEAM_REUSE`（config.py + .env.example 已声明）。
- M4 flag 不改变标准搜索默认行为；关闭后回到 M3 末态。
- 新增 M4 能力必须可关闭、必须有回滚或安全降级路径、不得改变当前排序。

## 七、禁止字段（开发报告/JSON/导出文件）

只允许：字段名、count、状态、reason code、feature flag 状态、元数据、来源锚点、用户自填短字段、指标摘要、测试结果和结论。

不得保存：原始 query、案情正文、候选正文、chunk 正文、裁判文书正文、用户输入自由长文本。本合同自身 `this_artifact_contains_body_text=false`。

## 八、验证

详见本步骤验证命令与 `m4-workflow-entry-contract-20260613-090354.json` 的 `go_no_go` 字段。新增 focused 合同测试 `tests/test_m4_workflow_entry_contract.py` 已纳入本步骤验证。

## 九、止损复核

M3-8 产物齐备、M3 结论仍为 GO、默认开关未被打开、合同保证持久层只存元数据和来源锚点——**止损未触发**，允许继续 M4-2。
