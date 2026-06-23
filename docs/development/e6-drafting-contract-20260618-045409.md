# E6-1 文书工作台入口合同与 DraftDescriptor 契约确认 · 验收报告

- **里程碑**：E 系列多产品生态全链路闭环 · E6 文书辅助工作台
- **步骤**：E6-1（合同确认步，对标 E-1 / E4-1 / E5-1）
- **生成时间**：2026-06-18T04:54:09+08:00
- **结论**：**GO**，允许进入 E6-2
- **性质**：只确认契约对象 + 落 sanitize 纯函数 + 守门测试 + 文档登记，零业务实现、零端点、零前端、零写库、零行为变化。DraftDescriptor 在 E-1 已冻结，本步是「确认 + 落纯函数」，区别于 E5-1 新增 StatuteRef——但 E6 引用 E5 法条需为 DraftDescriptor 追加可选 statute_refs，故走合同变更登记。

---

## 1. 前置门禁（入场基线）

| 项 | 要求 | 实际 | 结论 |
| --- | --- | --- | --- |
| E5 release gate | overall=GO/CONDITIONAL_GO | `e5-release-gate-20260618-022200.json` overall=GO | PASS |
| 进入 E6 许可 | allow_enter_e6=true | E5-7 收官 `allow_enter_e6=true` | PASS |
| 基础搜索 + intake + statute | E5 三类全 GO | E5 gate 三类全 GO | PASS |

前置满足，E6-1 继续。

---

## 2. 契约确认结论

`DraftDescriptor` 是 E-1（文档 16 §4 / 17 §3.3）已冻结的**第 3 个跨产品契约对象**。本步确认其字段白名单、持久层边界、「只组装不起草」口径与 `ENABLE_DRAFTING` on 路径边界语义。

- **E-1 核心五字段保持逐位不变**（仍由 `whitelist.DRAFT_DESCRIPTOR_FIELDS` + `test_e1_contracts.py::test_draft_descriptor_fields_match_doc_s3` 守门）：
  `draft_id` / `structure_skeleton` / `candidate_refs` / `note` / `tag`。
- **合同变更登记（2026-06-18）**：E6 文书工作台需引用经 E5 互跳而来的法条 `StatuteRef`，故经文档 16 §4 / 17 §3.3 登记，为 `DraftDescriptor` 追加**可选** `statute_refs` 字段（沿 StatuteRef §3.5 登记范式，**非擅自加字段**）。
- **持久层元数据**（同 CaseFolder，由后端补，非起草正文）：`created_at` / `updated_at` / `owner_user_id` / `team_id` / `visibility`（默认 `private`）。
- **E6 权威白名单** 冻结为独立常量 `DRAFT_DESCRIPTOR_E6_FIELDS`（= 核心五字段 + `statute_refs` + 持久层元数据），落地于 `drafting_contract.py`。该常量 **不进** `CONTRACT_FIELD_WHITELIST`（E-1 四对象口径不动，与 `STATUTE_REF_FIELDS` 独立冻结同理）。

### 字段白名单（与文档 16 §4 / 17 §3.3 一致）

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `draft_id` | str(短) | 是 | 草稿标识 |
| `structure_skeleton` | list[str(短)] | 是 | 段落标题清单（非正文，单项 ≤ 60 字） |
| `candidate_refs` | list[CandidateRef] | 是 | 引用的检索结果（缺锚点 fail-closed 丢弃） |
| `statute_refs` | list[StatuteRef] | 否 | 引用的法条（经 E5 互跳；缺锚点丢弃）── E6-1 合同变更追加 |
| `note` | str(短) | 否 | 用户自填短备注（≤ 200 字） |
| `tag` | str(短) | 否 | 用户自填短标签（≤ 40 字） |
| `created_at`/`updated_at` | datetime | 否 | 持久层时间戳（后端补） |
| `owner_user_id`/`team_id` | str(短) | 否 | 多租户字段（后端补） |
| `visibility` | enum(private/team) | 否 | 默认 `private` |

---

## 3. 新增 / 修改文件

**新增**

- `apps/api/app/kernel/guardrails/contracts/drafting_contract.py` — DraftDescriptor / DraftCandidateRef 模型 + 白名单常量 + 四类禁键集合 + sanitize/assert 纯函数。
- `apps/api/tests/test_e6_drafting_contracts.py` — 42 个 focused 单测（契约确认 + 四类禁键 + 标题校验 + 缺锚点丢弃 + 公开面身份保持 + AST 边界 + flag/router 不变）。
- `docs/development/e6-drafting-contract-20260618-045409.md`（本报告）
- `docs/development/e6-drafting-contract-20260618-045409.gate.json`
- `docs/development/e6-drafting-contract-20260618-045409.parameter.json`

**修改（公开面导出 + 合同变更登记）**

- `apps/api/app/kernel/guardrails/contracts/__init__.py` — 再导出 DraftDescriptor 契约符号（身份保持）。
- `apps/api/app/kernel/guardrails/__init__.py` — 内核护栏公开面再导出 DraftDescriptor 契约符号（身份保持）。
- `落地设计文档/16-多产品生态全链路闭环设计文档.md` — §4 登记 DraftDescriptor 追加可选 statute_refs（合同变更）。
- `落地设计文档/17-E系列分步骤文档.md` — §3.3 同步契约登记（字段表 + 红线 + 登记说明）。
- `落地设计文档/21-E6文书工作台分步骤系统提示词文档.md` — E6-1 落地勘误小节。

---

## 4. sanitize 规则（fail-closed，异常只回键名 / reason code）

`sanitize_draft_descriptor(payload)`：

1. **拒四类禁键**（NO_GO 级，不静默丢弃）：
   - ①起草正文型：`draft_body`/`draft_content`/`generated_text`/`opinion_text`/`paragraph_body`/`conclusion_text`/... + 通用正文黑名单（`whitelist.FORBIDDEN_BODY_KEYS`）。
   - ②裁判正文型：`chunk_text`/`judgment_text`/`summary_text`/`highlight_text`/`matched_text`/... + 富展示型（与 E5 `STATUTE_FORBIDDEN_DISPLAY_KEYS` 同口径）。
   - ③PII 型：复用 intake `is_forbidden_pii_key`（姓名/证件/联系方式/金融/地址/车牌）。
   - ④胜负/结论型：`win_probability`/`outcome_prediction`/`predicted_outcome`/`verdict`/`litigation_outcome`/...
   - 兜底：模型生成条文型（引用法条时）`generated_article`/`llm_text`/`paraphrased_article`/...
2. **白名单收敛**：仅保留 `DRAFT_DESCRIPTOR_E6_FIELDS`，其余非白名单键主动丢弃。
3. **structure_skeleton 标题校验**：非空标题字符串；单项 > 60 字（疑似正文）→ reason `SKELETON_ITEM_TOO_LONG`；空项 → `SKELETON_ITEM_NOT_TITLE`；列表空 → `SKELETON_EMPTY`；项数 > 64 → `SKELETON_TOO_MANY_ITEMS`。
4. **引用必带锚点**：`candidate_refs` 逐项收敛 E-1 七字段 + `source_anchors`（case_id+source_chunk_id），缺/不完整锚点整条 **丢弃**；`statute_refs` 经 E5 `sanitize_statute_ref` 收敛，缺 `statute_anchors`（无 text_id）整条 **丢弃**（但 statute 内禁键仍 fail-closed 抛错）。保留项 100% 有锚点。
5. **extra=forbid**：模型层再兜一层；非白名单键直接拒绝。

`assert_no_draft_body(payload)`：断言对象 + 嵌套 `candidate_refs`/`statute_refs`/`related_case_refs` 内不含任何起草正文/裁判正文/胜负结论/模型生成型键。

异常消息只暴露**键名 / reason code / 结构性原因**，单测覆盖「原始敏感值不出现在异常字符串」。

---

## 5. 公开面导出方式（身份保持）

- 经 `app.kernel.guardrails`（`from contracts import ...` 再导出）与 `app.contracts`（E-2b shim 自动 `dir()` 转发）双面可达。
- 单测 `test_public_face_identity_preserved` 断言：`guardrails.DraftDescriptor is contracts.DraftDescriptor is drafting_contract.DraftDescriptor`，`sanitize_draft_descriptor`/`assert_no_draft_body`/`DRAFT_DESCRIPTOR_E6_FIELDS` 同理 `is` 同一对象（不复制实现、不改签名）。

---

## 6. ENABLE_DRAFTING on 路径边界（本步只写语义，不实现）

- `ENABLE_DRAFTING`（E-1 已冻结，默认 `false`）= 文书工作台总开关。**off（默认）**：drafting 端点 403 安全降级、前端无入口（E6-2/E6-3 实现）。**on**：端点只做「组装」——接收带锚点的 `CandidateRef`/`StatuteRef` + 标题骨架 + 短字段，经 `sanitize_draft_descriptor` 收敛后持久化（只存元数据/引用/短字段），绝不起草段落正文、不补全结论、不输出胜负判断。
- E6 **不新增任何对外业务 flag**，**不引入 AI 起草开关**——「不起草正文」是结构性红线，不能用 flag 放开。
- 本步不接线、不依赖 `ENABLE_DRAFTING` 的 on 路径。

---

## 7. 验证结果

| 验证项 | 命令 | 结果 |
| --- | --- | --- |
| E6 契约单测 | `pytest tests/test_e6_drafting_contracts.py` | **42 passed**（VM，临时装 pydantic/sqlalchemy/sqlmodel/fastapi） |
| E1 + E2a + E2b 回归 | `pytest tests/test_e1_contracts.py tests/test_e2a_kernel_boundary.py tests/test_e2b_shim_equivalence.py` | **全绿**（VM） |
| include_router 计数 | `grep -cE "app\.include_router" app/main.py` | **14**（未接线，符合预期） |
| 产品包 | `ls app/ \| grep -E "drafting\|casebook"` | **无**（未建产品包） |
| flag 默认值 | `config.py` | `ENABLE_DRAFTING=False` / `ENABLE_CASEBOOK=False` |
| py_compile | drafting_contract.py + 两个 __init__.py | OK |

**E4/E5 既有回归（VM 下报错，非 E6-1 改动所致）**：VM 多挂载快照串扰（traceback 出现 `compassionate-inspiring-newton` / `friendly-eager-darwin` 等非当前挂载），导致 pytest 收集到**陈旧测试副本**：

- E4：`NameError: name 'o'`（line 640 被字符截断；当前挂载 host 行为 `offenders: list[str] = []`，干净）。
- E5：旧 `test_include_router_count_still_13` 断言 `14 == 13` 失败——这恰好**证明 main.py 现为 14**（E5-4 reconciled），旧断言才是 stale；当前挂载源文件已是 `test_no_drafting_casebook_product_package` + `FORBIDDEN=("drafting","casebook")`。

这类 VM 字符截断 / 多挂载 stale 属 E5-6/E5-7 已记录的环境工件，**须以 host `.venv311`（Py3.11.9）复核为权威口径**。

---

## 8. 隐私 / 红线扫描

| 维度 | 结果 |
| --- | --- |
| 起草正文 | 0 命中（白名单零正文字段 + 四类禁键 fail-closed + 单测全 dump 扫描） |
| 裁判正文 | 0 命中 |
| PII | 0 命中（复用 intake 黑名单 + fixture 只用短假数据） |
| 胜负/结论 | 0 命中 |
| 凭据 | 0 命中（无凭据相关字段/import） |
| 禁用文案 | 0 命中 |

---

## 9. 结论

- DraftDescriptor 契约确认并文档化；核心五字段与文档 16 §4 / 17 §3.3 逐字段一致；E6 追加可选 `statute_refs` 经合同变更登记（非擅自加字段）。
- `drafting_contract.py` 落地（纯数据 + 纯函数）：fail-closed 拒四类键 + structure_skeleton 标题校验 + 引用缺锚点丢弃。
- 公开面导出可达、身份保持；不接线、不建包、不建端点、不写库；`include_router` 仍 14。
- 起草正文 0 / 裁判正文 0 / PII 0 / 胜负结论 0 / 凭据 0 / 禁用文案 0 命中。
- **overall = GO，allow_enter_e6_2 = true**。
- 下一步：**类案检索助手 E6-2 drafting 后端包与 gated 端点**。
