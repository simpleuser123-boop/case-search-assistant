# E3-1 内部服务契约冻结 · 验收报告

- 步骤：E3-1（E 系列多产品生态 · E-3 检索内部服务接口之契约冻结步）
- 时间：2026-06-15T16:22:02
- 比对基线：E-2 末态 `docs/development/e2b-release-gate-20260615-113552.json`
- 入场门禁：`docs/development/e3-entry-check-20260615-153349.json` = **GO**
- 结论：**GO — 允许进入 E3-2**
- 性质：只冻结 E3 内部服务契约（纯 Python 模型 + 纯函数 + 测试）。**不接入真实检索链路、不改 `/api/search`、不注册任何 HTTP 端点、不新建产品包**。

---

## 1. 本步交付的 E3 子目标

把「检索助手」的内部服务**契约面**冻结为机器可校验口径：

```text
SearchProfile（脱敏、白名单输入）
  -> 检索内部服务（E3-2 才实现真实链路）
  -> CandidateRef[]（只含元数据 + source_anchors，零正文）
```

本步只定义输入/输出契约的 Python 形态、白名单约束与 sanitize 规则，并经内核公开面导出稳定符号。真实检索执行（适配层）留待 E3-2。

## 2. 新增 / 修改文件

| 文件 | 类型 | 说明 |
|---|---|---|
| `apps/api/app/kernel/rag/internal_search_contracts.py` | 新增 | E3 契约模型 + sanitize 纯函数（289 行） |
| `apps/api/tests/test_e3_internal_search_contracts.py` | 新增 | E3 契约 focused 测试（39 passed） |
| `apps/api/app/kernel/rag/__init__.py` | 修改 | 仅追加 E3 契约符号的 re-export + `__all__` 条目 |
| `docs/development/e3-service-contract-20260615-162202.md` | 新增 | 本报告 |
| `docs/development/e3-service-contract-20260615-162202.json` | 新增 | 机器可读 gate |

> `app.kernel.__init__` 未直接改动：它通过 `from app.kernel.rag import *` + `list(rag.__all__)` 自动继承 E3 符号，已验证可从 `app.kernel` 导入且与底层为同一对象。

## 3. 契约字段（与 E-1 白名单逐字段一致）

### SearchProfile（输入）
`case_cause` / `region` / `trial_level_preference` / `dispute_focus_keywords` / `query_text`
- 与 `SEARCH_PROFILE_FIELDS` 逐字段相等（测试断言）。
- `query_text` 为已脱敏短查询；原始口语化案情不在契约内（仅浏览器本地）。
- `extra="forbid"`：非白名单 / 正文型键在模型层即拒绝。

### CandidateRef（输出）
`case_id` / `case_number` / `court` / `trial_level` / `case_cause` / `judgment_date` / `source_anchors`
- 与 `CANDIDATE_REF_FIELDS` 逐字段相等（测试断言）。
- `case_number` 由 `SearchResultItem.case_no` 映射（输出字段名固定为 `case_number`）。
- `source_anchors` 非空，每条 = `SourceAnchorRef(case_id, source_chunk_id, anchor_type?)`。

### 内部参数模型
- `InternalSearchRequest`：`profile` + `mode(standard/expanded)` + `limit` + `include_relaxed_recall`；`extra="forbid"` 拒绝 `raw_case/raw_query`。
- `InternalSearchResult`：`candidate_refs` + `degraded` + `degraded_reasons` + 可选 `coverage/timings`；不含正文。
- `InternalSearchMode`：`Literal["standard","expanded"]`（仅透传，不改召回/排序）。

### 纯函数
- `sanitize_search_profile(payload) -> SearchProfile`
- `sanitize_candidate_ref(payload) -> CandidateRef`
- `search_result_item_to_candidate_ref(item) -> CandidateRef`（只取白名单 + 锚点）

## 4. 正文泄露防线（fail-closed，三层）

1. **E-1 黑名单层**：复用 `sanitize_contract` + `FORBIDDEN_BODY_KEYS`，命中 `raw_*/full_text/content/chunk_text/body/...` 立即抛 `ContractViolationError`。
2. **E3 富展示键层**：E-1 黑名单未收录的 `summary/highlights/matched_text/highlight_text/summary_text/holding_summary` 等富展示字段，E3 在 `_reject_e3_body_keys` 显式拒绝（文档 18 §10 止损线将其列为 NO_GO）。**未改 E-1 白名单/黑名单本身**。
3. **模型层**：所有契约模型 `extra="forbid"`，任何漏网的非白名单键在 Pydantic 构造时再拒一次。

此外：
- `source_anchors` 为空 / 缺失 / 锚点缺 `case_id` 或 `source_chunk_id` → fail-closed 抛错，不暴露不可溯源候选（复用内核单点 `is_valid_anchor`）。
- `search_result_item_to_candidate_ref` 只搬运白名单字段与锚点；`summary/highlights/matched_text/metadata` 一律不进入输出（测试逐键断言不存在）。

## 5. 验证命令与结果

```bash
cd apps/api
pytest tests/test_e1_contracts.py tests/test_e2a_kernel_boundary.py tests/test_e3_internal_search_contracts.py
# => 70 passed in 1.28s（24 + 7 + 39），0 failed
```

| 文件 | passed |
|---|---|
| test_e1_contracts.py | 24 |
| test_e2a_kernel_boundary.py | 7 |
| test_e3_internal_search_contracts.py | 39 |
| **合计** | **70** |

> 环境说明：本会话 VM 为全新实例，`.venv311`/`.venv` 为 Windows 二进制不可用。用 system Python 3.10 经 files.pythonhosted.org 直链 wheel 安装 pydantic==2.7.1 / pydantic-settings==2.2.1 / pytest==8.2.0 / SQLAlchemy==2.0.30 / sqlmodel==0.0.18（pip 索引页过大被代理截断，改用单包直链 URL 绕过）。仅为跑测装依赖，未改任何源码依赖版本。

## 6. 边界合规（未越界）

| 项 | 结论 |
|---|---|
| 新建产品包（intake/statute/drafting/casebook） | 无（四者均 absent） |
| 新增 HTTP 端点 | 无；`include_router` 仍为 12（M1~M5 既有） |
| `/api/search` 是否改动 | 未改（mtime `Jun 15 09:46`，本会话前） |
| 契约模块 import 检索运行时 | 否（AST 扫描断言） |
| 契约模块注册 router/FastAPI | 否（文本扫描断言） |
| flag 默认值变化 | 无（`ENABLE_WEIGHTED_RERANK`/`ENABLE_ECOSYSTEM` 等仍 false） |

## 7. 公开面与身份保持

- `SearchProfile / CandidateRef / InternalSearchRequest / InternalSearchResult / InternalSearchMode / SourceAnchorRef / sanitize_search_profile / sanitize_candidate_ref / search_result_item_to_candidate_ref` 均可从 `app.kernel.rag` 与 `app.kernel` 导入，且均在各自 `__all__` 中。
- `app.kernel.rag.CandidateRef is app.kernel.CandidateRef is internal_search_contracts.CandidateRef`（身份保持，无分叉）。

## 8. 隐私 / 正文扫描

- 新增代码与产物中无 `raw_query/raw_case/full_text/chunk_text/content/body` 作为数据字段（仅作为「被拒绝键名」常量出现）。
- 测试 fixture 只用短假数据 / 假案号 / `case_id` / `source_chunk_id`，无真实长案情或裁判正文。
- 无密钥打印；无确定性夸大/结果担保/胜负预测类禁用文案（按 tendency_gate 禁用词口径核验，无命中）。

## 9. E3-1 结论

| 门禁 | 结论 |
|---|---|
| entry_gate（E3-0） | GO |
| contract_gate（白名单 + sanitize） | GO |
| surface_gate（公开面 + 身份保持） | GO |
| boundary_gate（无产品包/端点/深引/未改 search） | GO |
| regression_gate（70 passed / 0 failed） | GO |
| flag_gate（默认全 false 未变） | GO |
| **E3-1 总判定** | **GO** |

- **是否允许进入 E3-2**：**允许**。
- **下一步标题：类案检索助手 E3-2 检索执行服务适配层**
