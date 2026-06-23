# M5-1 商业化扩展入口合同

- 生成时间：2026-06-14T03:02:48+08:00
- 步骤：M5-1（商业化扩展入口合同）
- 配套产物：`m5-commercial-entry-contract-20260614-030248.json`
- 范围：冻结 M4-8 三类 GO 入场证据；定义 M5 多租户数据边界、鉴权边界、凭据安全边界、共享/导入/倾向分析/计费合同字段、**服务端多用户持久化字段白名单**与**凭据安全红线**。**仅声明合同，不实现** M5-2 及之后任何账号、隔离、权限、共享、导入、倾向分析或计费业务行为。

## 一、Go/No-Go 结论

| 结论项 | 判定 |
| --- | --- |
| M5-1 商业化扩展入口合同 | **GO** |
| 允许进入 M5-2 | 是 |
| 是否需要回滚 | 否 |
| 止损是否触发 | 否 |

下一步新会话标题：**类案检索助手 M5-2 账号体系与认证骨架**。

## 二、M5 entry register（入场证据冻结）

入场依据为 M4-8 验收产物，三类结论与 M5 入口均为 GO：

- `docs/development/m4-final-verification-20260614-102758.md`
- `docs/development/m4-release-gate-20260614-102758.json`
- `docs/development/m4-parameter-version-20260614-102758.json`

| 结论项 | 状态 |
| --- | --- |
| 基础搜索继续可用 | GO |
| M4 工作流沉淀完成 | GO |
| M5 入口 | GO |
| 是否需要回滚 | 否 |

### M4-1 至 M4-7 最新产物与状态

| 步骤 | 能力 | 产物前缀 | 状态 |
| --- | --- | --- | --- |
| M4-1 | 工作流沉淀入口合同 | m4-workflow-entry-contract-20260613-090354 | GO |
| M4-2 | 检索历史与草稿恢复 | m4-search-history-draft-20260613-103734 | GO |
| M4-3 | 案例收藏能力 | m4-case-favorite-20260613-113516 | GO |
| M4-4 | 类案清单组装 | m4-case-list-20260613-211225 | GO |
| M4-5 | 类案清单导出 | m4-list-export-20260614-004136 | GO |
| M4-6 | 轻量报告模板生成 | m4-report-template-20260614-014251 | GO |
| M4-7 | 团队复用能力评估 | m4-team-reuse-assessment-20260614-015309 | GO |

### M4-7 五项 not_ready 能力与已预留字段位

M4-2 至 M4-6 全部沉淀能力都是**纯前端 localStorage、单浏览器、单用户、零服务端持久层**；后端 `db.py` 仅有引擎、无业务表、无用户身份概念。团队复用是从"无服务端用户态"到"多用户协作"的体系级跃迁，归 M5。M4-7 仅评估并预留字段位（默认私有、默认关闭共享）。

| 能力 | 就绪度 | 已预留字段位 | data_structure_reserved |
| --- | --- | --- | --- |
| account_system 账号体系 | not_ready | `owner_user_id` | 否 |
| list_sharing 清单/收藏共享 | not_ready | `owner` / `visibility` / `shared_with_team_id` | 是 |
| permission_tiering 权限分级 | not_ready | `role` / `permission_level` | 否 |
| team_workspace_isolation 团队空间与隔离 | not_ready | `team_id` / `workspace_id` | 是 |
| bulk_import 批量导入 | not_ready | `import_batch_id` / `import_source` | 否 |

预留默认值与 M4-1 合同一致：`owner=private`、`visibility=private`、`sharing=disabled`、`team_id=null`、`owner_user_id=null`。这些字段位在 M4-7 **仅声明，未注入在用 TS 类型或服务端表**；物理落地由 M5-2 起逐步连同服务端持久层一并完成。

### M2/M3/M4 仍需继承的门禁

- **来源**：来源锚点最小字段 `case_id` + `source_chunk_id`；案例侧 AI 内容必须有锚点，无锚点隐藏或安全降级。
- **隐私**：用户原始案情不持久化到服务端；持久层、报告、JSON、日志、导出文件、测试快照只存元数据/引用/用户自填短字段，不存正文。
- **文案**：不输出"已查全""保证无遗漏""查全率"；不输出胜诉/败诉概率或确定性法律结论。
- **排序**：不改主排序、source selection、rerank 默认开关；qrels/label/relevance 仅离线；不以 query id / case id 做运行时特判。
- **性能**：主搜索 warm P95 < 3s（M4-8 实测 820ms）。
- **回滚**：新能力必须可关闭或安全降级，关闭后回到上一里程碑末态。

### ENABLE_WEIGHTED_RERANK 与 6 个 M4 flag 当前证据

- `apps/api/app/core/config.py:42`：`ENABLE_WEIGHTED_RERANK: bool = False`。
- `apps/api/app/core/config.py:52-57`：6 个 M4 flag（`ENABLE_SEARCH_HISTORY` / `ENABLE_CASE_FAVORITE` / `ENABLE_CASE_LIST` / `ENABLE_LIST_EXPORT` / `ENABLE_REPORT_TEMPLATE` / `ENABLE_TEAM_REUSE`）默认全 `False`。
- `.env.example:23`（weighted rerank=false）、`:33-38`（后端 M4 flag=false）、`:60-70`（VITE 前端 flag=false）。
- M5-1 决策：`KEEP_FALSE`，不在本步或后续 M5 默认开启。

## 三、M5 商业化扩展合同字段（仅声明，不实现业务）

合同共定义 7 类字段，详见 JSON `commercial_contract_fields`。M5-1 仅声明，全部 `implemented_in_m5_1=false`、`affects_ranking=false`、`logs_events_reports_may_store_body=false`。

| 字段 | feature flag | 关键边界 |
| --- | --- | --- |
| `account` | ENABLE_ACCOUNT_SYSTEM | `user_id` + 认证状态；密码仅存单向哈希，SSO/OAuth 仅存 provider+subject 引用；不含明文凭据/令牌 |
| `team` | ENABLE_TEAM_WORKSPACE | `team_id`/`workspace_id`/成员关系；按团队强隔离，`team_id` 为空等同单用户私有 |
| `membership_role` | ENABLE_PERMISSION_TIERING | `owner`/`editor`/`viewer`；默认最小权限，显式授权才扩大；对象默认 private |
| `shared_object` | ENABLE_TEAM_SHARING | 收藏/清单/报告引用 + `visibility` + `shared_with_team_id`；默认 private、显式授权；锚点继承；不上送正文 |
| `bulk_import_job` | ENABLE_BULK_IMPORT | 来源/项数/锚点校验状态；导入即元数据/引用 only；缺锚点降级或拒绝；不导入正文 |
| `tendency_analysis` | ENABLE_TENDENCY_ANALYSIS | F19 统计口径/样本量/覆盖范围/数据门禁状态；门禁达标才展示；不预测个案、不输出胜负概率 |
| `billing_plan` / `subscription` | ENABLE_BILLING | 套餐/试用/续费意愿；仅脱敏回执引用；**不含任何支付凭据**，不代填、不代管明文 |

## 四、服务端多用户持久化字段白名单（M5 最关键产物）

M5-2 至 M5-9 **只能**向服务端多用户持久层写入下列白名单字段；M4 白名单全量继承，仅新增结构化关系字段，**不放宽正文禁令**。未声明字段一律不落库。

### 允许持久化

**继承 M4 白名单全部字段**：`case_id`、`case_number`、`court`、`trial_level`、`case_cause`、`judgment_date`、`source_anchors`、用户自填 `note`/`tag`/`label`、`list_id`/`list_title`/`report_id`/`export_id`、结构化关系 `structured_relation`、时间戳 `created_at`/`updated_at`/`timestamp`、`status`、`reason_code`、`feature_flag_state`。

**M5 新增结构化关系字段**：`owner_user_id`、`team_id`、`workspace_id`、`visibility`、`role`、`permission_level`、`shared_with_team_id`、`bulk_import_job_id`、`billing_plan_id`、`subscription_status`。

### 禁止持久化

原始 `raw_query`、案情正文 `case_fact_body`、自由文本长片段、候选正文 `candidate_body`、chunk 正文 `chunk_body`、裁判文书长正文 `judgment_long_text`、摘要/要旨/对比正文型内容、用户输入自由长文本、未脱敏个人信息。M4 禁令全量生效。

### 边界保证

- `user_raw_case_persisted_on_server=false`、`draft_body_persisted_on_server=false`。
- 白名单只含元数据、引用（来源锚点）、用户自填短字段和结构化关系字段，不含任何正文型内容。
- allowed ∩ forbidden = ∅（合同测试断言）。

## 五、凭据安全红线清单

| 红线 | 要求 |
| --- | --- |
| 密码 | 仅存单向哈希（如 bcrypt/argon2）+ 盐；**绝不明文存储** |
| SSO/OAuth 令牌 | 不写入服务端业务表、日志、JSON、报告或测试快照；仅存 provider + subject 引用 |
| 会话令牌 | 仅在传输与受保护存储中使用，不落业务表/日志/产物 |
| 支付凭据 | 卡号、银行账户、CVV、第三方支付令牌明文一律不落库、不入日志、不入产物 |
| 政府证件号 | 一律不写入服务端业务表、日志、JSON、报告或测试快照 |
| 代填/代管 | 工具**绝不**代填、代管、代存任何登录或支付凭据明文；敏感输入由用户/平台侧完成 |
| 支付流程 | 跳转平台侧/第三方支付完成；工具只记录脱敏回执引用（`payment_ref_hash` + status） |

允许保留的脱敏/哈希引用：`payment_ref_hash`、`auth_subject_ref`、`user_id_hash`。本合同自身 `this_artifact_contains_plaintext_credentials=false`。

## 六、锚点继承规则（继承 M4）

- 共享、导入、报告、倾向分析引用的案例侧 AI 内容必须可追溯 `case_id` + `source_chunk_id`。
- 无锚点内容不进入共享对象、导入结果、报告或倾向分析交付物（`no_anchor_no_delivery=true`）。
- 不伪造 `source_chunk_id`。
- 适用范围：`shared_object`、`bulk_import_job`、`report_template`、`tendency_analysis`。

## 七、M5 feature flag 策略

- `ENABLE_WEIGHTED_RERANK=false` 与 6 个 M4 flag 默认 `false` 继续保持（`KEEP_FALSE`）。
- 新增 7 个可关闭开关，默认安全态 `false`：`ENABLE_ACCOUNT_SYSTEM`、`ENABLE_TEAM_WORKSPACE`、`ENABLE_PERMISSION_TIERING`、`ENABLE_TEAM_SHARING`、`ENABLE_BULK_IMPORT`、`ENABLE_TENDENCY_ANALYSIS`、`ENABLE_BILLING`（已写入 `config.py` + `.env.example`）。
- M5 flag 不改变标准搜索默认行为；关闭后回到 M4 末态（单用户、纯前端沉淀）。
- 新增 M5 能力必须可关闭、有回滚或安全降级路径、不改当前排序、不默认开启跨用户可见性、不代管凭据明文。

## 八、禁止字段与凭据安全边界（开发报告/JSON/导出文件）

只允许：字段名、count、状态、reason code、feature flag 状态、元数据、来源锚点、用户自填短字段、结构化关系字段、指标摘要、测试结果和结论。

不得保存：原始 query、案情正文、候选正文、chunk 正文、裁判文书正文、用户输入自由长文本、**任何凭据明文或令牌**（密码、SSO/OAuth 令牌、会话令牌、卡号、银行账户、CVV、政府证件号）。本合同自身 `this_artifact_contains_body_text=false`、`this_artifact_contains_plaintext_credentials=false`。

## 九、本步不做

- 不实现 M5-2 之后的账号、认证、隔离、权限、共享、导入、倾向分析或计费业务。
- 不引入任何服务端多用户业务行为（仅声明合同与字段位）。
- 不默认开启任何 M5 flag、`ENABLE_WEIGHTED_RERANK` 或 M4 flag。
- 不默认开启跨用户可见性、不让共享默认非私有。
- 不改主排序、source selection、rerank 默认开关或 M2/M3/M4 默认行为。
- 不把 query id / case id 写入排序、共享、导入或倾向分析特判。
- 不修改 qrels、label、历史评测结果。

## 十、验证

- 后端核心门禁 7 文件：`test_m1_3_candidate_comparison.py`、`test_m1_3_legal_candidate_robustness.py`、`test_feature_flag_rollback.py`、`test_health.py`、`test_search_api_fallback_smoke.py`、`test_summary_service.py`、`test_performance_smoke.py`。
- 新增 focused 合同测试 `tests/test_m5_commercial_entry_contract.py` 已纳入本步骤验证。
- 详见 `m5-commercial-entry-contract-20260614-030248.json` 的 `go_no_go` 与 `stop_loss_checks` 字段。

## 十一、止损复核

M4-8 产物齐备、M4 三类结论仍为 GO、`ENABLE_WEIGHTED_RERANK` 与 6 个 M4 flag 默认 `false`、新增 7 个 M5 flag 默认 `false`、白名单保证服务端只存元数据/引用/结构化关系字段、凭据红线保证不代管明文——**止损未触发**，允许继续 M5-2。
