# E7-1 案件协作工作台入口合同与 CaseFolder 契约确认 — 验收报告

- 时间戳：`20260622-035431`
- 入场基线：`docs/development/e6-release-gate-20260622-013221.json`（`allow_enter_e7=true`）
- 结论：**GO**（`allow_enter_e7_2=true`）
- 对标：E5-1 / E6-1（合同确认步，零业务实现）

## 1. 契约确认结论

`CaseFolder` 是 E-1 已冻结的**第 4 个跨产品契约对象**。本步是「确认 + 落 sanitize 纯函数」，**不走合同新增**（区别于 E5-1 新增 StatuteRef）。

E-1 §3.4 冻结的**核心九字段**保持不变（仍由 `whitelist.CASE_FOLDER_FIELDS` 与 `test_e1_contracts` 守门，逐位一致）：

```
case_folder_id / owner_user_id / team_id / visibility /
search_profile_summary / candidate_refs / draft_descriptors /
created_at / updated_at
```

E7 权威白名单冻结为**独立常量 `CASE_FOLDER_E7_FIELDS`** = 核心九字段 + 用户自填短字段 `title` / `note` / `tag`。该常量**不进** `CONTRACT_FIELD_WHITELIST`（E-1 四对象核心口径不动，与 `STATUTE_REF_FIELDS` / `DRAFT_DESCRIPTOR_E6_FIELDS` 独立冻结同理）。

> **合同变更登记口径**：`title/note/tag` 是文档 16 §4.1 红线、文档 17 §3.4 红线、文档 22 §1/§3.5 已列举的「用户自填短字段」（同 DraftDescriptor `note`/`tag` 范式，持久层短字段非正文）。但 E-1 §3.4 字段表未含此三键，故须回文档 16/17 补登合同变更登记。本步已在文档 22 §4 E7-1 小节记录，建议下一轮同步回 16/17（沿 E6-1 statute_refs 登记范式）。

## 2. 新增 / 修改文件

新增：

- `apps/api/app/kernel/guardrails/contracts/casebook_contract.py`（纯数据 + 纯函数）
- `apps/api/tests/test_e7_casebook_contracts.py`（26 个 test 函数）
- `docs/development/e7-casebook-contract-20260622-035431.md` / `.json`

修改（仅追加 re-export + `__all__`，身份保持）：

- `apps/api/app/kernel/guardrails/contracts/__init__.py`
- `apps/api/app/kernel/guardrails/__init__.py`
- `落地设计文档/22-E7案件协作工作台分步骤系统提示词文档.md`（E7-1 小节收口）

`app/contracts/__init__.py` 是 E-2b shim，遍历 `dir(_real)` 自动转发 — **无需改动**，`CaseFolder` / `sanitize_case_folder` 经 shim 身份保持可达。

## 3. sanitize 规则

`sanitize_case_folder(payload) -> CaseFolder`（fail-closed，异常只回键名 / reason code）：

1. 显式拒绝四类禁止键：①裁判正文型 ②起草正文型 ③PII 型 ④胜负/结论型（含 `case_summary_text` 自动综述正文）。
2. 只保留 `CASE_FOLDER_E7_FIELDS` 白名单键，其余主动丢弃。
3. `search_profile_summary` 走 E4 `sanitize_intake_search_profile` 同口径：正文/PII 键 fail-closed，只保留 SearchProfile 脱敏白名单子集键。
4. `candidate_refs` 逐项收敛（`CaseFolderCandidateRef` = CandidateRef 白名单七字段）；缺锚点项 fail-closed 丢弃，保留项 100% 有 `source_anchors`。
5. `draft_descriptors` 逐项走 E6 `sanitize_draft_descriptor`；缺骨架丢弃、禁止键抛错，内层引用缺锚点亦丢弃。
6. `visibility` 缺省（含空串）补 `private`；非空非法值 fail-closed 拒绝。
7. 输出只含白名单字段、零裁判正文、零起草正文、零原始案情。

`assert_no_case_body(payload)`：递归断言 `candidate_refs` / `draft_descriptors`（及内层 `candidate_refs`/`statute_refs`）+ `search_profile_summary` 内无四类禁止键。

## 4. 公开面导出方式

经 `app.kernel.guardrails.contracts`（源）→ `app.kernel.guardrails`（re-export）→ `app.contracts`（shim 自动转发）三面导出，**身份保持**（`is` 同一对象），不回引聚合。导出符号见同名 JSON。

## 5. ENABLE_CASEBOOK 边界

`ENABLE_CASEBOOK` 默认 `false`；本步只写 on 路径边界语义 + 默认 `visibility=private`，**不实现端点**（端点/多租户落库在 E7-2）。

## 6. 验证

- VM 隔离 harness（纯 pydantic，不触发 `app.kernel` 重依赖）：28/28 逻辑断言全过。
- `py_compile`：`casebook_contract.py` + `contracts/__init__.py` 通过。
- AST 边界：`casebook_contract` 不 import retrieval/rerank/summary/kernel.rag/任何产品包/router/fastapi/sqlmodel/sqlalchemy；相对兄弟 import 仅 `whitelist`/`intake_contract`/`statute_contract`/`drafting_contract`。
- `include_router` = **15**（未变）；`app/casebook` 产品包**不存在**。
- flag 默认 false：`ENABLE_CASEBOOK` / `ENABLE_INTAKE_AI_EXTRACTION` / `ENABLE_WEIGHTED_RERANK`。
- 隐私扫描：裁判正文 0 / 起草正文 0 / 原始案情 0 / PII 0 / 胜负结论 0 / 凭据 0 / 禁用文案 0。
- host `.venv311`（Python 3.11.9 / pydantic 2.7.1 / pytest 8.2.0）全链路 import 通过：
  - `app.contracts.CaseFolder is app.kernel.guardrails.CaseFolder is app.kernel.guardrails.contracts.CaseFolder`
  - `sanitize_case_folder` / `assert_no_case_body` 三面身份保持
  - 默认 `visibility=private`
- host `.venv311` pytest 复跑通过：`292 passed, 1 warning in 2.44s`（warning 为 Starlette `python_multipart` PendingDeprecationWarning）。

本次 host 权威复跑命令：

```
cd apps/api && export DATABASE_URL="sqlite:///./test_e7.db"
pytest tests/test_e7_casebook_contracts.py
pytest tests/test_e1_contracts.py tests/test_e2a_kernel_boundary.py tests/test_e2b_shim_equivalence.py
pytest tests/test_e4_intake_boundaries.py tests/test_e5_statute_contracts.py tests/test_e6_drafting_contracts.py
```

复跑前发现 3 条旧快照断言仍停在 E5 口径（`drafting` 不得存在 / `include_router=14`），与 E6 收官事实冲突。已做**纯测试基线调和**，零业务代码改动：

- `tests/test_e2a_kernel_boundary.py`：E5 产品包快照上移到 E6 收官基线（`intake/statute/drafting` 合法，`casebook` 仍禁止）。
- `tests/test_e5_statute_contracts.py`：`drafting` 合法放行、`casebook` 仍禁止，`include_router` 断言 `14 -> 15`。

> **VM stale-mount 记录**：host Edit 后 `guardrails/__init__.py` 的 VM 挂载视图停滞在 162 行（截断于 E5 块），host Read 确认实盘 184 行 `__all__` 正常闭合。**未对 VM 做任何写回**（避免把截断版写回 host 破坏文件）；host `.venv311` 读实盘正确。

## 7. 是否允许进入 E7-2

**允许**（`allow_enter_e7_2=true`），host `.venv311` 权威复跑已将本步收口为 GO。

下一步：**类案检索助手 E7-2 casebook 后端包与 gated 端点**。
