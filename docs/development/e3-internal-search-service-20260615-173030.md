# E3-2 检索执行服务适配层 · 验收报告

- 步骤：E3-2（E 系列多产品生态 · E-3 检索内部服务接口之检索执行服务适配层）
- 时间：2026-06-15T17:30:30
- 比对基线：E3-1 契约冻结 `docs/development/e3-service-contract-20260615-162202.json` = **GO**
- 上游基线：E-2b 末态 `docs/development/e2b-release-gate-20260615-113552.json`
- 结论：**GO — 允许进入 E3-3**
- 性质：实现 Python 内部服务适配层。**不改 /api/search 路由消费方式、不注册任何 HTTP 端点、不新建产品包、不改前端、不改排序/召回/summary 策略、不写库**。

---

## 1. 本步交付的 E3 子目标

把现有「查询处理 / 召回 / 排序 / 摘要展示准备 / 来源锚点校验」组织成可复用的内部检索服务：

```text
InternalSearchRequest(SearchProfile)
  -> InternalSearchService.execute(...)               -> InternalSearchExecutionResult（富结果，供 E3-3 复用 /api/search 行为）
  -> InternalSearchService.search_candidate_refs(...)  -> InternalSearchResult（跨产品输出，只含 CandidateRef[]，零正文）
```

- 富执行结果（`InternalSearchExecutionResult`）只承载内核级对象，供 E3-3 在 `/api/search` 内用既有 helper 映射 `SearchResponse`。
- 跨产品输出（`search_candidate_refs`）只暴露 `CandidateRef[]`，严格受 E-1 白名单约束，零正文。

## 2. 新增 / 修改文件

| 文件 | 类型 | 说明 |
|---|---|---|
| `apps/api/app/kernel/rag/internal_search_service.py` | 新增 | E3-2 检索执行服务适配层（服务类 + 富结果 + 纯转换函数，402 行） |
| `apps/api/tests/test_e3_internal_search_service.py` | 新增 | E3-2 服务 focused 测试（15 passed） |
| `apps/api/app/kernel/rag/__init__.py` | 修改 | 追加 E3-2 服务符号的 re-export + `__all__` 条目 |
| `docs/development/e3-internal-search-service-20260615-173030.md` | 新增 | 本报告 |
| `docs/development/e3-internal-search-service-20260615-173030.json` | 新增 | 机器可读 gate |

> `app.kernel.__init__` 未直接改动：它通过 `from app.kernel.rag import *` + `list(rag.__all__)` 自动继承 E3-2 符号，已验证可从 `app.kernel` 导入且与底层为同一对象（身份保持）。

## 3. 服务公开面

可从 `app.kernel.rag` 与 `app.kernel` 导入（身份保持，已运行时断言）：

- `InternalSearchService`：内部检索服务适配层。
  - `execute(request, *, query_session_id=None) -> InternalSearchExecutionResult`：富执行结果，供 E3-3 复用 /api/search。
  - `search_candidate_refs(request, *, query_session_id=None) -> InternalSearchResult`：跨产品输出，只含 `CandidateRef[]`。接受 `SearchProfile` 或 `InternalSearchRequest`。
- `InternalSearchExecutionResult`：富结果（dataclass），承载 `query_plan / case_candidates / results / low_confidence_candidates / presentation_by_rank / degraded / degraded_reasons / timings / search_mode` 及早退信号 `query_validation_error / retrieval_error_type`。
- `CANDIDATE_REF_DROPPED_NO_ANCHOR`：锚点不完整候选被丢弃时的降级原因码。

依赖经构造函数注入（默认用内核公开面服务类），便于测试用 fake/mock 替换。

## 4. 设计原则落地

- **经公开面消费内核**：服务从各 RAG 子包公开面（`query_processing / rerank / retrieval / retrieval.confidence / summary`）import 符号，不深引私有实现、不引旧路径（`app.retrieval` 等已不存在）。
  - 注：未从 `app.kernel.rag` 聚合 `__init__` 回引——本模块由该聚合 `__init__` 装配时 import，直接回引会触发部分初始化的循环导入；改走子包公开面（与 E3-1 契约模块同款规避），仍在 `app.kernel.*` 边界内。
- **零正文跨产品输出**：`search_candidate_refs` 只把可见候选转成 `CandidateRef`，经 `sanitize_candidate_ref`（E-1 白名单 + 锚点 fail-closed）。`summary / highlights / matched_text / metadata` 一律不进入输出。`case_no -> case_number` 映射。
- **日志脱敏**：服务日志只写 `query_session_id / input_hash / 计数 / degraded_reasons`，**绝不写 query_text / 原始案情**。已扫描确认源码无 `query_text=` 等数据字段写法。
- **不改排序/召回/summary**：`execute` 编排顺序与 `api/search._handle_search_request` 完全一致（merge -> rerank -> split -> presentations -> degraded 汇总），不为提指标改排序。
- **SearchProfile 结构化字段**：`case_cause / region / trial_level_preference / dispute_focus_keywords` 本步仅随 `InternalSearchRequest.profile` 保留透传，未新增复杂检索策略（查询文本仍只走 `query_text`）。
- **不写库**：服务不持有持久层句柄，不写搜索历史 / 收藏 / 报告。

## 5. 临时重复逻辑（E3-3 将消除）

`execute()` 的编排与 `api/search.py::_handle_search_request` 当前为**同形最小重复**（同序、同 helper 语义）。本步按提示词约束**不改 /api/search**，故暂存重复。

- 风险控制：服务编排与 search.py 逐步对齐（相同的 `merge_case_candidates / split_low_confidence_candidates / SUMMARY_LLM_UNAVAILABLE` 降级口径），不引入行为分叉。
- 候选 -> `CandidateRef` 的锚点/字段映射逻辑（`_safe_candidate_ref / _source_chunk_ids`）是 search.py `_candidate_to_result` 中**最小无副作用转换子集**，只取 E-1 白名单 + 锚点，不构造 `SearchResultItem`。
- **E3-3 处置**：让 `/api/search` 改为消费 `InternalSearchService.execute()`，删除 search.py 内重复编排，使二者收敛为单一权威实现。富结果 `InternalSearchExecutionResult` 已为此预留全部内核级中间产物。

## 6. 验证命令与结果

```bash
cd apps/api
pytest tests/test_e3_internal_search_contracts.py tests/test_e3_internal_search_service.py
# => 54 passed（39 契约 + 15 服务）

pytest tests/test_e1_contracts.py tests/test_e2a_kernel_boundary.py tests/test_e2b_shim_equivalence.py
# => 80 passed（24 + 7 + 49）

# 合并一次性复跑：134 passed in 2.57s
```

| 文件 | passed |
|---|---|
| test_e3_internal_search_contracts.py | 39 |
| test_e3_internal_search_service.py | 15 |
| test_e1_contracts.py | 24 |
| test_e2a_kernel_boundary.py | 7 |
| test_e2b_shim_equivalence.py | 49 |
| **合计** | **134** |

> 环境说明：本会话 VM 为全新实例。用 system Python 3.10 经 pip 安装 pydantic==2.7.1 / pydantic-settings==2.2.1 / pytest==8.2.0 / SQLAlchemy==2.0.30 / sqlmodel==0.0.18（本次标准索引可用，未走直链 wheel）。仅为跑测装依赖，未改源码依赖版本。

### E3-2 服务测试覆盖项

- 公开面可导入 + 身份保持（app.kernel / app.kernel.rag）。
- `search_candidate_refs` 接受 `SearchProfile` 与 `InternalSearchRequest`，输出 `CandidateRef[]`。
- `CandidateRef` 严格白名单（逐字段断言 == E-1 7 字段；`FORBIDDEN_ON_CANDIDATE_REF` 全不存在）。
- expanded 模式把 `include_relaxed_recall=True` 透传到 retrieval；coverage.search_mode=expanded。
- **fail-closed**：锚点不完整候选被丢弃，记 `CANDIDATE_REF_DROPPED_NO_ANCHOR`，good 候选仍输出。
- degraded/degraded_reasons 透传（如 `CHROMA_EMPTY`）；timings 只含整数毫秒字段，无正文。
- QueryValidationError / 召回异常 -> 空候选 + degraded（不抛）；summary 异常不打断候选输出。
- `execute` 富结果承载内核级对象（query_plan / case_candidates / presentation_by_rank / SearchTimings）。
- 静态：服务模块不 import fastapi / app.schemas / app.api；不深引非 kernel 旧路径；无 APIRouter/@router；源码无 query_text/raw_* 数据字段写法。

## 7. 是否改变外部 /api/search 行为

**否。** `apps/api/app/api/search.py` 本步未改动（`grep "InternalSearch\|internal_search" = 0` 引用）。路由消费方式、`SearchResponse` 结构、错误码、降级行为、日志脱敏口径均无变化。`/api/search` 切换到内部服务是 E3-3 的工作。

## 8. 边界合规（未越界）

| 项 | 结论 |
|---|---|
| 新建产品包（intake/statute/drafting/casebook） | 无（四者均 absent） |
| 新增 HTTP 端点 | 无；服务模块无 APIRouter/@router/add_api_route；`include_router` 仍为 12 |
| `/api/search` 是否改动 | 未改（0 引用新服务） |
| 服务 import 旧路径 / 私有实现 | 否（只经 app.kernel.* 子包公开面；静态测试断言） |
| 引入 app.schemas / FastAPI | 否（静态测试断言） |
| 排序 / 召回 / summary 策略 | 未改（编排同序，无新策略） |
| 真实写库 / 写历史 / 写收藏 / 写报告 | 无 |
| flag 默认值变化 | 无（ENABLE_WEIGHTED_RERANK/QUERY_REWRITE/SUMMARY/EXPANDED_SEARCH/ECOSYSTEM 仍 false） |

## 9. 隐私 / 正文扫描

- 新增服务代码：日志只写 `query_session_id / input_hash / 计数 / degraded_reasons`；无 `query_text= / raw_query= / raw_case= / full_text= / chunk_text= / judgment_full_text` 数据字段写法（grep 确认）。
- 正文型键仅作为「被拒键名常量 / 注释 / docstring」出现，不作数据字段。
- 跨产品输出 `CandidateRef` 不含 `summary / highlights / matched_text / metadata / chunk_text / full_text / content / body`（白名单 + sanitize 双保险，测试逐键断言）。
- 测试 fixture 只用短假数据 / 假案号 / `case_id` / `source_chunk_id` / 元数据，无真实长案情或裁判正文。
- 无密钥打印；无「已查全 / 保证无遗漏 / 胜诉概率」等禁用文案（grep 确认无命中）。

## 10. E3-2 结论

| 门禁 | 结论 |
|---|---|
| contract_gate（E3-1 契约 GO 前置） | GO |
| service_gate（InternalSearchService 可用 + CandidateRef 输出） | GO |
| surface_gate（公开面 + 身份保持） | GO |
| boundary_gate（无产品包/端点/深引/未改 search/未引 schemas） | GO |
| privacy_gate（白名单 + 零正文 + 日志脱敏 + 无禁用文案） | GO |
| regression_gate（134 passed / 0 failed） | GO |
| flag_gate（默认全 false 未变） | GO |
| **E3-2 总判定** | **GO** |

- **临时重复逻辑**：存在（execute 与 search.py 同形最小重复 + 候选转换子集），已说明，E3-3 消除，无行为分叉。
- **是否允许进入 E3-3**：**允许**。
- **下一步新会话标题：类案检索助手 E3-3 搜索 API 消费内部服务**
