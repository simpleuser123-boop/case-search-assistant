# M5 商业化扩展最终验收报告（M5-10 收官）

- **里程碑**：M5 商业化扩展
- **步骤**：M5-10 — M5 验收与商业化就绪结论
- **生成时间**：2026-06-15 06:33:29
- **依据设计文档**：落地设计文档/15-M5商业化扩展分步骤文档.md、01-演进计划总览.md、04-数据层设计.md
- **入场依据**：docs/development/m4-final-verification-20260614-102758.md

---

## 一、三类结论（摘要）

| 结论项 | 判定 |
|---|---|
| 基础搜索继续可用 | **GO** |
| M5 商业化扩展完成 | **GO** |
| 商业化就绪（团队采购、协作、计费可用） | **GO** |

> 三项均 GO。M5 为演进路线图末段里程碑，收官后建议另起 **M6-1 运营与规模化入口合同**。

---

## 二、M4-8 入场证据复核

| 项 | 期望 | 实测 | 结论 |
|---|---|---|---|
| 基础搜索 | GO | GO | ✅ |
| M4 工作流沉淀 | GO | GO | ✅ |
| M5 入口 | GO | GO（conclusions.m5_entry=GO） | ✅ |
| ENABLE_WEIGHTED_RERANK | false | false | ✅ |
| 6 个 M4 flag 默认 | false | 全 false | ✅ |
| M4 七步状态 | 全 GO | 全 GO | ✅ |
| 回滚要求 | 无 | rollback_required=false | ✅ |

入场证据完整，无 M4-8 核心门禁回归。

---

## 三、M5-1~M5-9 能力状态汇总

| 步骤 | 能力 | 产物 | 结论 |
|---|---|---|---|
| M5-1 | 商业化扩展入口合同 | m5-commercial-entry-contract-20260614-030248.json | GO |
| M5-2 | 账号体系与认证骨架 | m5-account-system-20260614-044809.json | GO |
| M5-3 | 团队空间与数据隔离 | m5-team-workspace-isolation-20260614-055955.json | GO |
| M5-4 | 权限分级 | m5-permission-tiering-20260614-065238.json | GO |
| M5-5 | 沉淀同步与团队共享 | m5-sync-and-sharing-20260614-080403.json | GO |
| M5-6 | 批量导入 | m5-bulk-import-20260614-093728.json | GO |
| M5-7 | F19 倾向分析数据门禁(v2) | m5-tendency-data-gate-20260614-134042.json | GO（gate=pass, f19_can_go_live=true） |
| M5-8 | F19 展示与边界 | m5-tendency-analysis-20260615-024038.json | GO |
| M5-9 | 商业化闭环 | m5-billing-20260615-042028.json | GO |

九步全部 GO，前置门禁满足。

---

## 四、门禁复跑结果

### 4.1 后端（pytest 指定集 + M5 focused tests）

环境：VM 依赖重置后按 recipe 重装（pydantic 2.13.4 + core 2.46.4、fastapi 0.111.0、starlette 0.37.2、sqlmodel、httpx、pytest 等，DATABASE_URL=sqlite 规避 psycopg2）。

| 批次 | 测试文件 | passed |
|---|---|---|
| 1 | candidate_comparison + legal_candidate_robustness + feature_flag_rollback + health | 41 |
| 2 | search_api_fallback_smoke + summary_service + performance_smoke | 19 |
| 3 | m5_commercial_entry_contract + m5_account_system + m5_team_isolation + m5_permission | 66 |
| 4 | m5_sharing + m5_bulk_import + m4_team_reuse | 54 |
| 5 | m5_tendency_gate + m5_tendency_analysis + m5_billing | 66 |
| **合计** | | **246 passed / 0 failed** |

### 4.2 前端（vitest + vite build）

`npm run test` 全量 >44s VM 超时，按记忆 recipe 分批运行（≤5 文件/批），逐批 exit=0：

| 范围 | passed |
|---|---|
| components（account/billing/tendency/bulkImport/team/permission/sharing） | 24 |
| lib（9 文件） | 130 |
| services（8 文件） | 32 |
| pages（9 文件） | 89 |
| **合计** | **275 passed / 0 failed** |

`npx vite build`：**118 modules transformed，built clean，exit=0**。

---

## 五、硬门禁复核

### 5.1 服务端持久层白名单（16 张 M5 表）

逐字段核对 8 个 M5 包的 model 文件，全部为白名单内字段：主键/外键 id、单向哈希、状态短枚举、计数、用户自填短字段（display_name/note/tag/label/renewal_reason）、结构化关系字段（owner_user_id / visibility / team_id / role）、来源锚点 JSON（source_anchors，仅 case_id+source_chunk_id）。

- **无任何正文列**（full_text/quanwen/content/body/parties/defendant/plaintiff/fact_text/chunk_text 等均不存在）。
- **无任何凭据列**：账号 `password_hash`（单向哈希，可空）；会话 `token_hash`（哈希，原始 token 绝不落库/入日志）；计费 `payment_ref_hash`（单向哈希），**无卡号/银行账户/CVV/令牌列**。

### 5.2 多租户隔离

唯一过滤点 `app/team/isolation.py::tenant_visibility_clause`：

- 单用户私有态：`owner_user_id == ctx.owner AND team_id IS NULL`，看不到任何团队行/他人行。
- 团队态：自己的私有行 OR（本团队 team_id 且 visibility=team）；**跨团队 team_id 不匹配在 SQL 层即被排除，他人 private 行不可见**。
- store 层无「无过滤读取」方法，所有读取强制拼接该 clause。
- 写入经 `assert_write_within_tenant`：写入 team_id 必须与上下文一致，team 可见性须团队态，visibility 限已知短枚举。

跨团队/跨用户串读：**SQL 层排除，门禁通过**。

### 5.3 对象级访问控制

权限分级 owner3/editor2/viewer1/none0；private 对象不因团队角色自动放权，须 owner 显式授权（m5_object_grant）。越权读写被拒并记审计 `m5_permission_audit`（`actor_user_id_hash` + `object_id_hash`，**审计不含正文/凭据**）。M5-4 产物 no_go_checks 全 clear。

### 5.4 共享默认私有

`m5_shared_object` 仅作显式共享动作账本，不参与读取放权；SedimentationObject.visibility 默认 `private`。跨用户可见性非默认开启，共享须显式授权。

### 5.5 凭据安全

- 密码单向哈希、会话 token 仅存哈希、支付仅存 payment_ref 单向哈希。
- **工具内绝不代填/代管/代存支付或登录凭据**（M5-9 红线，billing 包不接收卡号/CVV/银行/令牌字段，schema extra=forbid → service → store 写前 assert 三重防线）。
- 源码 + 产物真实凭据/令牌字面量扫描 = **0 命中**（卡号正则误命中 `region_missing_rate` 浮点小数位，已确认为浮点数非凭据）。

### 5.6 用户原始案情

用户原始 query / 案情正文未上送服务端持久层；沉淀对象仅元数据 + 来源锚点引用。

### 5.7 F19 来源可追溯与边界

- 聚合 `app/tendency_analysis/aggregate.py`：每 bucket 仅收集 `case_id_refs`（截断 ≤20），`case_id_total` 计数，**不收集任何正文字段**。
- 双闸：service.build() 在 `ENABLE_TENDENCY_ANALYSIS=false` 或 `f19_can_go_live=false` 时 raise TendencyUnavailable → API 403；返回前 `assert_analysis_output_clean` fail-closed。
- **无个案预测/概率/确定性法律结论**（护栏拦截胜负话术 + 具名法官预测正则）。

### 5.8 主排序与默认行为不变

8 个 M5 包**均不 import rerank/retrieval/scoring/ranker/search**（仅注释声明不 import，实查零真实导入）。主排序、source selection、rerank 默认开关未变。

### 5.9 qrels/label/relevance 与 id 特判

- qrels/label/relevance **未进入**账号/团队/共享/导入/倾向分析/排序。两处 `label` 命中均为良性：team 透传用户自填组织标签、tendency bucket 展示名（court level 名称），非相关性标注。
- query id / case id **未用于运行时特判**。

---

## 六、扫描结果

| 扫描项 | 命中 | 结论 |
|---|---|---|
| 正文/隐私键（持久层/产物/导出/共享/导入/F19 输出） | 0 | ✅ PASS |
| 凭据明文/令牌（密码/SSO/OAuth/卡号/银行账户/CVV/政府证件号） | 0（误命中已澄清） | ✅ PASS |
| 禁用绝对话术（已查全/保证无遗漏/查全率/胜败诉概率/确定性结论/个案预测） | 0 | ✅ PASS |

---

## 七、性能与回滚

- **性能**：performance_smoke 断言 warm P95 = 820ms < 3s（`p95_under_3s=True`），主搜索 warm P95 达标。
- **主链路**：M5 能力不造成主链路白屏（flag 关闭时组件渲染 null / 端点 403）。
- **回滚**：
  - 每个 M5 flag 可关闭；关闭后端点返回 403 并回到上一里程碑末态（account→M4、billing→M5-8、tendency 双闸→M5-7 末态等，均有 focused 测试覆盖）。
  - M4 四个 flag 回滚 + 事件日志测试通过。
  - ENABLE_WEIGHTED_RERANK 与 M4/M5 flag 未被默认开启。

---

## 八、Flag 默认值核对

config.py（行 31-70）与 .env.example（行 12-105）一致：weighted_rerank + 3 个 M2/M3 + 6 个 M4 + 7 个 M5 flag **全部默认 false**，前端 13 个 VITE_ 镜像 flag 全 false。**.env.example 未把任何 flag 默认改为 true**。

---

## 九、止损（NO_GO）触发项核对

下列 15 项止损条件**全部为 false（未触发）**：正文泄露、凭据明文/令牌落库或代填、跨租户串读、越权未拦截、默认非私有共享、原始案情上送、无来源 AI 引用、F19 违规上线或个案预测、绝对话术、qrels/label/relevance 进入运行时、id 特判、flag 默认误开、M4-8 门禁回归、M5 不可回滚或主链路不可用。

---

## 十、最终判定

1. **基础搜索继续可用：GO** — health/fallback/candidate robustness/性能/回滚全通过。
2. **M5 商业化扩展完成：GO** — 账号/隔离/权限/共享/导入/F19/计费九步全 GO，门禁全过。
3. **商业化就绪：GO** — 无正文泄露、无凭据明文/令牌、无跨租户串读、无越权放行、共享默认私有、无绝对话术、无个案预测、无默认开关误开、无质量回归；团队采购、协作、计费链路具备且默认安全关闭、可显式启用、可回滚。

### 下一阶段建议

进入「运营与规模化」：数据规模化迁移 Milvus、F19 持续扩样、商业化指标跟踪与迭代。另起里程碑文档：**类案检索助手 M6-1 运营与规模化入口合同**。

