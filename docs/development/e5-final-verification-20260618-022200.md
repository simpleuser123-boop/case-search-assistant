# E5 验收与下一步门禁（E5-7 收官报告）

- **生成时间**：2026-06-18T02:22:00+08:00
- **里程碑**：E 系列多产品生态全链路闭环 — E5 法条法规检索
- **基线**：`docs/development/e4-release-gate-20260617-112542.json`（GO，allow_enter_e5=true）
- **overall**：**GO**（前序 CONDITIONAL_GO 经 codex host .venv311 复跑全绿后升 GO）
- **allow_enter_e6**：true
- **host 复跑权威 runner**：codex `.venv311`（Py3.11.9）

---

## 1. 结论速览

E5 已达成「法条法规检索」目标。statute 落地为 E 系列第 2 个产品包，提供 3 个 flag-gated 端点；查询 / CandidateRef 入，带 `text_id` 锚点的 `StatuteRef[]` 出；法条条文 100% 锚定语料、catalog 模式零杜撰、无锚点不展示；法条↔类案双向互跳只走契约对象，两侧均无对侧裁判正文；statute 经 `app.kernel.rag.StatuteSearchService` 消费内核公开面、不深引检索底层、不 import 其它产品包；复用既有向量库 + `law_articles` 标注，法条索引与案件索引物理隔离，案件产物与评测基线未改；`include_router=14`、`ENABLE_STATUTE_SEARCH` 默认 false、无前端默认入口、无新增默认 true 业务 flag。

判 **GO**：E5-1~E5-6 的 CONDITIONAL 唯一条件项（后端 pytest 须 host `.venv311` 实跑）已由 codex 在 host 复跑闭合 —— E5 后端全套 227 passed、E1~E3 回归 178 passed、E4 回归 126 passed、历史核心烟测 60 passed、3 个已知 NameError 单点 3 passed、`include_router=14`、前端 build 通过、statute flags 默认 false（唯一 Starlette PendingDeprecationWarning 非阻塞）。沿用 E3-6/E4-6 由 codex host 复跑闭环的既定口径。

---

## 2. 各子步门禁汇总

| 子步 | 标题 | 判定 | 说明 |
|------|------|------|------|
| E5-1 | 入口合同与法条契约冻结 | GO | StatuteRef 冻结为第 5 契约对象，文档16/17 登记，extra=forbid |
| E5-2 | 法条语料与标注管道 | GO | 182 法条/100% 锚点/catalog 零杜撰，索引隔离，案件产物未改 |
| E5-3 | 内核法条检索服务 | GO | 三入口 + 互跳 + fail-closed；后端 pytest host 复跑闭合 |
| E5-4 | statute 后端包 + gated 端点 | GO | 3 端点 403 降级，消费内核服务；后端 pytest host 复跑闭合 |
| E5-5 | 前端法条检索页 | GO | 双重 flag 门控，条文只渲后端 article_text，build 123 modules |
| E5-6 | 消费边界与护栏守门 | GO | 11 类守门全绿；2 条陈旧断言 E5-7 已调和、host 复跑通过 |

E5-3/E5-4/E5-6 的前序 CONDITIONAL 条件（后端 pytest 须 host `.venv311` 实跑）已由 codex host 复跑统一闭合，全部升 GO。

---

## 3. 本会话验证结果

### 3.1 前端（VM 权威）

- `vite build`：**PASS，123 modules transformed**（E4 末态 121 + 2 个 gated statute 文件；dist EPERM 经 `--outDir /tmp` 旁路）。
- `vitest`（分批，逐文件）：触碰用例 **64 passed**
  - `statuteApi.test.ts` 8 passed
  - `StatutePage.test.tsx` 5 passed
  - `featureFlags.test.ts` 9 passed
  - `HomePage.test.tsx` 9 passed（回归）
  - `SearchPage.test.tsx` 33 passed（回归）

### 3.2 后端（host .venv311 权威，由 codex 复跑闭合）

本会话 VM 取证：`fastapi / sqlalchemy / sqlmodel / pydantic / pydantic_settings / pytest` 全部缺失，后端 pytest 无法在 VM 跑。codex 已在 host `.venv311`（Py3.11.9）复跑确认：

- 3 个已知 NameError 单点（截断残片补回后）：**3 passed**
- E5 后端全套：**227 passed**
- E1~E3 回归：**178 passed**
- E4 回归：**126 passed**
- 历史核心烟测：**60 passed**
- `test_e5_statute_api_run.py` 额外确认：**25 passed**
- `include_router_count=14`，`intake_router / statute_router` 均已接线
- `npm run build` 通过，123 modules transformed
- statute flags 默认仍 false
- 唯一提示：Starlette `PendingDeprecationWarning`（非阻塞，不影响验收）

codex 仅补回 4 个测试文件被 VM-mount 截断的残片（纯测试、零业务代码）：`test_e5_statute_api.py:454`（`ass`→`assert`）、`test_e5_statute_api_run.py:454`、`test_e3_internal_search_boundaries.py:375`、`test_e4_intake_boundaries.py:640`。

---

## 4. 强制扫描结果（本会话 VM 静态，全过）

| # | 扫描项 | 结果 |
|---|--------|------|
| 1 | statute 代码裁判正文型字段（full_text/content/chunk_text/summary_text/highlight_text/matched_text）真实赋值 | 0 |
| 2 | statute 代码模型生成条文键（generated_article/llm_text/paraphrased_article/...）真实引用 | 0 |
| 3 | 密钥打印（api_key/secret/token/DEEPSEEK/sk-） | 0 |
| 4 | 禁用文案（已查全/保证无遗漏/胜诉概率/...） | 0 |
| 5 | drafting / casebook 产品包 | 均不存在 |
| 6 | include_router 计数 | 14 |
| 7 | statute router 端点数 | 3 |
| 8 | 越界端点（/api/internal、/api/ecosystem） | 0 |
| 9 | ENABLE_STATUTE_SEARCH 默认 | False（config.py:80） |
| 10 | .env.example statute flags | 均 false（行 59 / 129） |
| 11 | statute 包 import 其它产品包（intake/drafting/casebook） | 0 |
| 12 | statute 深引 retrieval/rerank/summary 内部 | 0 |
| 13 | statute 经 kernel.rag.StatuteSearchService 消费 | 是 |
| 14 | statute_chunks 锚点完整率 | 182/182 = 100% |
| 15 | 案件产物 hash vs E5-2 基线 | 逐字节一致 |
| 16 | 索引隔离（statute vs case collection） | 隔离（名 + persist 目录均异） |
| 17 | statute 管道写案件 collection | 0（仅"绝不写入"注释引用） |
| 18 | case_statute_links 覆盖 | 2505 案 / 10792 refs / 100% |
| 19 | statutes.jsonl article_text（catalog 模式） | 0（零杜撰） |

---

## 5. E5-7 测试调和（纯测试文件，零业务代码改动）

E5-6 遗留的陈旧断言全部位于 `apps/api/tests/test_e5_statute_contracts.py`（E5-1 期编写，post-E5-4 已被 `test_e5_statute_boundaries.py` 正确取代）。本步调和为 E5-4 后实况：

1. `PRODUCT_PACKAGES=("statute","drafting","casebook")` → `FORBIDDEN_PRODUCT_PACKAGES=("drafting","casebook")`（statute 合法放行）。
2. `test_no_statute_drafting_casebook_product_package` → `test_no_drafting_casebook_product_package`。
3. `test_include_router_count_still_13`（断言 13）→ `test_include_router_count_is_14`（断言 14）。
4. `test_no_statute_endpoint_registered`（断言不接线，语义已 E5-4 翻转）→ `test_statute_router_wired_post_e5_4`（断言 statute_router 已 import + include）。
5. 模块 docstring 与 §8 section header 同步更新。

> E2/E3/E4 守门规则、statute/StatuteSearchService/InternalSearchService/intake 行为、端点、产品包均未改。Edit 经 host fs 直写（规避 VM-mount 截断陷阱），改后 4 处经 Read 工具逐行确认；codex host `.venv311` 复跑已确认 3 个改名断言通过（纳入 E5 全套 227 passed）。

---

## 6. 已知约束与 host 复核结果（已闭合）

- **后端 pytest 仅 host 可跑**：本会话 VM 缺全部后端依赖；已由 codex host `.venv311` 复跑后端 E5 全套（227 passed）+ E1~E3 回归（178 passed）+ E4 回归（126 passed）+ 历史核心烟测（60 passed）闭合。
- **3 个 VM-mount 截断 NameError**（`test_e3_internal_search_boundaries::test_e3_docs_have_no_long_body_text` / `test_e4_intake_boundaries::test_e4_docs_have_no_long_body_text` / `test_e5_statute_api::test_statute_source_has_no_body_field_literals`）：确认为 VM-mount 对含中文尾部文件的 tail 截断、且截断残片曾被写回 host。codex 在 host 补回 4 处残片（`test_e5_statute_api.py:454` / `test_e5_statute_api_run.py:454` / `test_e3_internal_search_boundaries.py:375` / `test_e4_intake_boundaries.py:640`，纯测试、零业务代码），复跑后 3 个单点 **3 passed**，已消失。
- **法条覆盖范围**：仅刑事（刑法 182 条）；民事/行政为已知缺口，与 M5-7 路线 B 扩语料同一依赖，本期不强求。

---

## 7. 法条覆盖范围

| 维度 | 状态 |
|------|------|
| 刑事 | 已覆盖（中华人民共和国刑法，182 distinct articles，catalog 目录带 text_id 锚点，article_text 待 seed 补齐） |
| 民事 | 缺口 — 未覆盖 |
| 行政 | 缺口 — 未覆盖 |

---

## 8. 新增 / 修改文件（E5-7）

**新增**
- `docs/development/e5-final-verification-20260618-022200.md`（本报告）
- `docs/development/e5-release-gate-20260618-022200.json`
- `docs/development/e5-parameter-version-20260618-022200.json`

**修改（纯测试调和）**
- `apps/api/tests/test_e5_statute_contracts.py`（3 条陈旧断言 + docstring + section header；本会话）
- codex host 复跑补回 4 个测试文件截断残片（纯测试、零业务代码）：`test_e5_statute_api.py:454`、`test_e5_statute_api_run.py:454`、`test_e3_internal_search_boundaries.py:375`、`test_e4_intake_boundaries.py:640`

---

## 9. 下一步

**是否允许进入 E6**：允许（GO）。codex host `.venv311` 复跑后端 E5 全套（227）+ E1~E3 回归（178）+ E4 回归（126）+ 历史核心烟测（60）+ 3 个 NameError 单点（3）全通过、`include_router=14`、前端 build 通过、statute flags 默认 false，CONDITIONAL_GO 已正式升 **GO**，无剩余前置阻塞。

**下一步标题**：**类案检索助手 E6-1 文书工作台入口合同**
