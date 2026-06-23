# 类案检索助手 E6 验收报告（E6-6 收官）

- **步骤**：E6-6 E6 验收与下一步（E7）门禁
- **生成时间**：2026-06-22T01:32:21+08:00
- **产物时间戳**：20260622-013221
- **入场基线**：`docs/development/e5-release-gate-20260618-022200.json`（overall=GO，allow_enter_e6=true）
- **总判定**：**CONDITIONAL_GO**，**allow_enter_e7=true**
- **唯一条件**：后端 pytest（E6 全套 + E1~E5 回归 + smoke）须 host `.venv311`（Py3.11.9）codex 复跑全绿；满足后无条件升 GO（同 E6-2/E6-5/E5-7 范式）。

---

## 1. 总览

E6 落地 E 系列**第三个产品能力包**——文书辅助工作台 `drafting/`，在「检索 → 法律依据」之后接上「依据 → 文书骨架」闭环：用户把检索沉淀的 `CandidateRef`（类案）/ `StatuteRef`（法条，经互跳）组装成**结构骨架**，每个引用带可核验锚点，导出强制免责头。核心定位是**只组装锚定来源、不起草结论性正文、不输出胜负判断**。

E6-1~E6-5 全部 GO / CONDITIONAL_GO（条件项唯一为后端 pytest 须 host 实跑，各子步已分别 host 验证）。本步不写新业务，仅做全量验收 + 调和 E6-5 显式委派的 4 条陈旧断言（纯测试改动）。

---

## 2. 三类验收判定

### 2.1 基础搜索仍可用 — GO

drafting 仅新增 gated 端点 + 前端页 + 前端导出纯函数，**未触碰** `/api/search`、`/api/search/expand`、E3 `InternalSearchService`、intake、statute 端点行为，也未改排序/召回/source selection/rerank 默认。案件产物 `cases.jsonl` / `chunks.jsonl` 的 SHA256 与 E5 基线**逐字节一致**（`8fcb2ec4…` / `8f391e68…`），法条/案件索引隔离与评测基线未改。E1~E5 回归须 host 复跑确认（VM 缺后端依赖）。

### 2.2 文书工作台完成 — GO

- **第 3 产品包** `app/drafting/`：`__init__/models/store/schemas/service/router` 6 文件，仅依赖 `app.kernel` 公开面（`guardrails`/`identity`）+ 既有持久层；AST 证明不直连 `retrieval/rerank/summary/query_processing`、不 import `intake/statute/casebook`。
- **4 gated 端点**：`POST /drafts`（创建）/ `GET /drafts`（列出）/ `GET /drafts/{id}`（读取）/ `PUT /drafts/{id}`（更新）；均需登录，`ENABLE_DRAFTING=false` 默认 **403 DRAFTING_DISABLED** 安全降级。
- **持久层 13 列零正文**：`draft_id / owner_user_id / team_id / visibility / structure_skeleton / candidate_refs / statute_refs / note / tag / status / reason_code / created_at / updated_at`；无任何 body/judgment/outcome/credential 列；默认 `visibility=private` + 租户隔离（`_tenant_clause`）+ owner 校验，越权 404。
- **导出强制免责头**：前端纯函数（对标 M4-5），markdown/text 两格式各无条件注入 `DRAFT_EXPORT_DISCLAIMER_LINES`（共 3 处），不取 `article_text`、无锚点引用丢弃、无正文/胜负/结论；导出失败安全降级不影响主链路。
- **前端三重门控**：`VITE_ENABLE_DRAFTING=false` 时路由不注册 + 入口不渲染 + 组件返回 null；前端 build **126 modules**。

### 2.3 只组装不起草边界成立 — GO

- **零起草正文**：`service.assemble_draft = sanitize_draft_descriptor`（E6-1 纯函数），service 内零 LLM/模型/检索/文本生成调用（AST + 标记扫描双证）；DraftDescriptor 无起草正文/裁判正文/胜负/结论字段（AST 字段名扫描 0 命中）。
- **引用必带锚点**：缺锚点引用 fail-closed 丢弃，保留项 100% 有 `source_anchors` / `statute_anchors`。
- **零胜负/结论**：drafting 后端代码 `胜诉/败诉/胜率/必然/稳赢/包赢` 数据路径 0 命中（仅免责头否定式 + 黑名单常量 + 注释）。
- **持久层零正文**：13 列全为元数据/引用/短字段。
- **产品包互不 import**：`test_e2a` append-only 追加 6 条 E6 跨包规则（`E6_FORBIDDEN_PRODUCT_PACKAGES=(casebook,)`、`E6_ALLOWED_PRODUCT_PACKAGES=(intake,statute,drafting)`），未放宽 E2~E5。
- **drafting 不直连底层**：AST 0 违规，仅经 `app.kernel` 公开面。

---

## 3. 验证环境与口径

| 维度 | 环境 | 权威项 |
| --- | --- | --- |
| 后端 pytest | host `.venv311`（Py3.11.9，codex 复跑） | E6 全套 + E1~E5 回归 + smoke（**本步待 host 复跑收口**） |
| VM 静态 | VM Py3.10.12（缺 fastapi/sqlmodel/sqlalchemy/pydantic_core/pydantic-settings/pytest，无网络） | py_compile / AST 边界 / 隐私扫描 / flag / 列集 / router / 前端 build（**本会话全过**） |
| 前端 | vite 5.2.11 | build 126 modules（`--outDir /tmp` 旁路 EPERM） |

### VM 静态验证结果（本会话权威项）

- `py_compile`：drafting 6 文件 + E6 三测试 + main.py **PASS（exit 0）**
- AST 边界：drafting **0 违规**（无深引 retrieval/rerank/summary/query_processing；无 intake/statute/casebook 跨包 import；仅公开面）
- drafting 禁用字段名（AST 字段/参数扫描）：**0**
- 持久层列集：`DraftDescriptorRow` **13 列，零 body 列**
- `include_router`：**15**
- casebook 产品包：**不存在**
- 7 产品 flag + AI 抽取 flag：**默认全 false**
- drafting 端点：**4**，均 gated（403 DRAFTING_DISABLED）；默认 visibility=**private**
- 案件产物 hash：cases.jsonl / chunks.jsonl 与 E5 基线 **MATCH**
- 导出免责头注入：**3**；导出 `article_text` 读取：**0**；前端 storage 调用：**0**
- 前端 build：**PASS，126 modules transformed**

---

## 4. E6-6 测试调和（纯测试，零业务改动）

E6-5 gate 显式委派 4 条陈旧断言到 E6-6 统一上移（`is_vm_artifact=false`，host 同样失败，根因 = E6-2 合法变更 include_router 14→15 + 新增 drafting 包）：

| 文件 | 原 | 改 |
| --- | --- | --- |
| `test_e4_intake_boundaries.py` | `ALLOWED_PRODUCT_PACKAGES={intake,statute}` | `{intake,statute,drafting}` |
| `test_e4_intake_boundaries.py` | `test_include_router_count_is_13`（断言 14） | `test_include_router_count_is_15`（断言 15） |
| `test_e5_statute_boundaries.py` | `ALLOWED_PRODUCT_PACKAGES={intake,statute}` | `{intake,statute,drafting}` |
| `test_e5_statute_boundaries.py` | `test_include_router_count_is_14`（断言 14） | `test_include_router_count_is_15`（断言 15） |

与 E5-7 调和 `test_e5_statute_contracts.py` 3 条断言同范式：纯测试改动、零业务代码、未放宽 E2/E2a/E3/E4/E5 守门。

---

## 5. CONDITIONAL 项与 host 复核状态

| 条件项 | 状态 | 说明 |
| --- | --- | --- |
| 后端 pytest（E6 全套 + E1~E5 回归 + smoke） | **PENDING host 复跑** | VM 无后端依赖无网络，无法跑 TestClient；各子步 gate 已分别 host 验证，本步收口待 codex host `.venv311` 复跑全绿后无条件升 GO |
| E6-6 调和的 4 条断言 | host 文件已改为当前真相（router=15、drafting 合法） | VM py_compile 因 stale-mount 截断（`test_e4` line 644 读出 `sorted(DO` 残片）报 SyntaxError 系挂载假象；host Read 验证源文件完整正确，两处编辑完整落地 |

**VM stale-mount 坑（复发记录）**：host Edit 经 VM 挂载在 `test_e4_intake_boundaries.py:644` 读出尾部截断；host Read 验证该行完整为 `sorted(DOCS_DEV_DIR.glob("e4-*.md")) + sorted(DOCS_DEV_DIR.glob("e4-*.json"))`。遵守「绝不 VM 读全量写回」纪律，host 文件为权威；`wc`/`py_compile` 在 stale 态不可信。

---

## 6. 覆盖范围与未尽事项

- **覆盖**：E6-1 契约确认、E6-2 后端包+端点+持久化、E6-3 前端页、E6-4 导出+免责头、E6-5 守门，已逐步 GO/CONDITIONAL_GO 并在本步统一收口。
- **未尽（不阻塞 E6）**：① 后端 pytest 须 host 实跑收口（环境限制，非缺陷）；② 法条语料仍 catalog 模式（`article_text=null`，民事/行政为已知缺口，与 M5-7 路线 B 扩语料同一依赖，非 E6 范围）；③ drafting 团队放权（team_id 客户端可写）留后续步骤，本步默认单用户私有态。

---

## 7. 结论

- **overall**：CONDITIONAL_GO
- **allow_enter_e7**：true
- **include_router**：15（intake + statute + drafting）
- **隐私扫描**：正文 0 / 裁判正文 0 / 胜负·结论 0 / PII 0 / 凭据 0 / 禁用文案 0 — **全 0 命中**
- **下一步**：类案检索助手 E7-1 案件协作工作台入口合同（CaseFolder 聚合，复用 M5 多租户/权限/共享）

---

## 8. 产物清单

- `docs/development/e6-final-verification-20260622-013221.md`（本报告）
- `docs/development/e6-release-gate-20260622-013221.json`
- `docs/development/e6-parameter-version-20260622-013221.json`
- 调和（纯测试）：`apps/api/tests/test_e4_intake_boundaries.py`、`apps/api/tests/test_e5_statute_boundaries.py`
