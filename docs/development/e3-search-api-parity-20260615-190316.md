# E3-3 /api/search 消费内部检索服务 · 验收报告

- 步骤：E3-3（E 系列多产品生态 · E-3 检索内部服务接口之「/api/search 消费内部服务」）
- 时间：20260615-190316
- 比对基线：E3-2 服务适配层 `docs/development/e3-internal-search-service-20260615-173030.json` = **GO**
- 上游基线：E-2b 末态 `docs/development/e2b-release-gate-20260615-113552.json`
- 结论：**GO — 允许进入 E3-4**
- 性质：改 /api/search 内部实现以消费 `InternalSearchService.execute()`；**外部行为零变化**。不注册端点、不建产品包、不改前端、不改排序/召回/summary 策略、不写库、不持久化 SearchProfile/CandidateRef。

---

## 1. 本步交付的 E3 子目标

把 `/api/search` 与 `/api/search/expand` 的核心检索执行切到 E3 内部检索服务，消除 E3-2 暂存的「同形最小重复」编排，使检索主路径收敛为**单一权威实现**（内核服务内），API 层只保留「内核富执行结果 -> SearchResponse」的映射。

```text
SearchRequest(query, mode, limit)
  -> SearchProfile(query_text=payload.query)        # API 层内存构造，不写库/日志
  -> InternalSearchRequest                          # mode/limit/include_relaxed_recall
  -> InternalSearchService.execute(...)             # 单一权威编排（查询处理/召回/排序/摘要/锚点）
  -> InternalSearchExecutionResult（富结果）
  -> _execution_to_response(...)                    # 既有 helper 映射 SearchResponse（行为不变）
```

## 2. 新增 / 修改文件

| 文件 | 类型 | 说明 |
|---|---|---|
| `apps/api/app/api/search.py` | 修改 | 主路径改为消费 `InternalSearchService.execute()`；删除内联编排（process/retrieve/merge/rerank/split/summary）；新增 `_build_internal_search_service` / `_execution_to_response`；保留 `_candidate_to_result/_build_coverage/_source_anchor/...` 等单一权威映射 helper；移除随之失效的 `_elapsed_ms` 及 `perf_counter/TimingRecorder/merge_case_candidates/split_low_confidence_candidates/QueryValidationError/SUMMARY_LLM_UNAVAILABLE` import。 |
| `apps/api/tests/test_e3_search_api_parity.py` | 新增 | E3-3 行为兼容测试（10 passed）。 |
| `docs/development/e3-search-api-parity-20260615-190316.md` | 新增 | 本报告。 |
| `docs/development/e3-search-api-parity-20260615-190316.json` | 新增 | 机器可读 gate。 |

> 未改 `app/kernel/rag/__init__.py` / `app/kernel/__init__.py`：E3-2 已导出 `InternalSearchService / InternalSearchExecutionResult / InternalSearchRequest / SearchProfile` 等全部所需符号，本步无需新增导出。

## 3. 实现要点（对应提示词 1~6）

1. **保留 router 路径与 response_model**：`/api/search`、`/api/search/expand` 路径、`response_model=SearchResponse`、responses 错误码表均未变。
2. **入参不变**：`/api/search` 仍接受 `SearchRequest(query, mode, limit)`，前端无需传 SearchProfile。
3. **API 层转 SearchProfile**：`SearchProfile(query_text=payload.query)`，仅内存构造透传给服务；**不写持久层、不写日志**，日志仍只用 `input_hash`。
4. **由服务执行检索**：`InternalSearchService.execute()` 返回富执行结果，API 层用既有 helper 映射 `SearchResponse`。
5. **单一权威映射**：`_candidate_to_result / _build_coverage / _source_anchor / build_risk_hints` 仅此一份；检索主路径编排不再在 API 层重复（已收敛进内核服务）。
6. **导入方向正确**：`api/search.py` 仅从 `app.kernel.rag` 公开面导入内核符号（静态测试 `test_api_search_imports_only_kernel_public_face` 断言无深引）。

### 服务实例注入（保证既有回归集 monkeypatch 生效）

模块级 `query_processing_service / retrieval_service / rerank_service / summary_service` 仍保留，并在请求时经 `_build_internal_search_service()` 注入到 `InternalSearchService`。既有回归集（fallback_smoke / feature_flag_rollback 等）对这些模块级符号的 monkeypatch 因此继续生效，无需改测试。

## 4. 行为兼容性说明

- **SearchResponse 字段集稳定**：parity 测试逐一断言 response key 集合与既有一致（candidates/results/low_confidence_candidates/risk_hints/coverage/degraded/degraded_reasons/retrieval_duration_ms/timings/query_session_id），`candidates == results`。
- **错误码**：query 校验失败 -> 既有 400（QUERY_PUNCTUATION_ONLY/EMPTY/TOO_SHORT）/ 413（QUERY_TOO_LONG，状态码取自异常）；召回异常 -> 503 SEARCH_RETRIEVAL_FAILED；summary 异常不打断、降级 SUMMARY_LLM_UNAVAILABLE。
- **expanded disabled**：`ENABLE_EXPANDED_SEARCH=false` 时 `/api/search/expand` 仍 403 EXPANDED_SEARCH_DISABLED，且不触发召回（spy 断言）。
- **coverage/timings/degraded/risk_hints**：由同一组 helper 基于 execute 富结果构造，口径不变。
- **CandidateRef 跨产品输出**：`search_candidate_refs` 作为内部服务能力并存，但 `/api/search` 主路径走 `execute`（富结果 -> SearchResultItem），不影响现有前端结果页。

## 5. 行为差异及处置（均不影响外部响应）

- summary 异常日志名由 API 层 `search_summary_unhandled` 迁为服务层 `internal_search_summary_unhandled`；降级口径（SUMMARY_LLM_UNAVAILABLE）与 response 字段无差异。
- 新增服务层 `internal_search_completed` 日志（仅计数/hash/降级原因，无正文）；API 层 `search_completed` 仍保留。两者均脱敏。
- 处置：以上仅为内部可观测性日志措辞，提示词允许改内部实现；parity 测试断言 raw_query / chunk body / key 不入日志。

## 6. 验证命令与结果

```bash
cd apps/api
# 环境：全新 VM，system Python 3.10 + pip 安装 fastapi/pydantic/sqlmodel/pytest/httpx；DATABASE_URL=sqlite 覆盖（仅为跑测，未改源码默认）。
pytest tests/test_e3_internal_search_contracts.py tests/test_e3_internal_search_service.py tests/test_e3_search_api_parity.py
# => 64 passed（39 + 15 + 10）

pytest tests/test_m1_3_candidate_comparison.py tests/test_m1_3_legal_candidate_robustness.py \
       tests/test_feature_flag_rollback.py tests/test_health.py tests/test_search_api_fallback_smoke.py \
       tests/test_summary_service.py tests/test_performance_smoke.py tests/test_e1_contracts.py \
       tests/test_e2a_kernel_boundary.py tests/test_e2b_shim_equivalence.py
# => 140 passed
```

合计 **204 passed / 0 failed**。

## 7. 是否改变外部 /api/search 行为

**否（行为零变化）。** 路径、response_model、SearchResponse 字段、错误码、降级行为、coverage/timings/risk_hints 口径、日志脱敏均与 E3-3 前一致；仅内部实现切换为消费内核服务。前端无需修改。

## 8. 边界合规（未越界）

| 项 | 结论 |
|---|---|
| 新增 HTTP 端点 | 无（include_router 仍 12；search POST 仍 /api/search、/api/search/expand 两条） |
| 新建产品包（intake/statute/drafting/casebook） | 无（四者均 absent） |
| 深引内核私有实现 | 否（仅 app.kernel.rag 公开面；静态测试断言） |
| 排序/召回/source selection/rerank 默认 | 未改 |
| 真实写库/写历史/写收藏/写报告 | 无 |
| 持久化 SearchProfile / CandidateRef | 无 |
| flag 默认值变化 | 无（ENABLE_QUERY_REWRITE/WEIGHTED_RERANK/SUMMARY/EXPANDED_SEARCH/ECOSYSTEM 仍 false） |

## 9. 隐私 / 正文扫描

- `query_text=` 在 search.py 仅出现于 `SearchProfile(query_text=payload.query)` 内存构造（提示词第 3 条明确要求）+ 一处 docstring；不写持久层、不写日志。
- 日志只写 `query_session_id / input_hash / 计数 / degraded_reasons / error_type`；parity 测试断言 raw_query / chunk body / 密钥不入日志。
- 无 `raw_case / full_text= / chunk_text= / judgment_full_text` 数据字段写法。
- 无「已查全 / 保证无遗漏 / 胜诉概率」等禁用文案；无密钥打印。
- 新测试 fixture 仅用短假数据 / 假案号 / case_id / source_chunk_id / 元数据。

## 10. E3-3 结论

| 门禁 | 结论 |
|---|---|
| service_consumption_gate（主路径经 InternalSearchService.execute） | GO |
| api_parity_gate（SearchResponse 字段兼容） | GO |
| error_code_gate（400/413/503/403 一致） | GO |
| degrade_gate（degraded/coverage/timings/risk_hints 口径不变） | GO |
| log_sanitization_gate（只写 hash/session/计数，无正文） | GO |
| single_authority_gate（API 不再复制检索编排） | GO |
| boundary_gate（无端点/无产品包/无深引/不写库/不持久化契约） | GO |
| privacy_gate（白名单 + 零正文 + 无禁用文案） | GO |
| flag_gate（默认全 false 未变） | GO |
| regression_gate（204 passed / 0 failed） | GO |
| **E3-3 总判定** | **GO** |

- **是否允许进入 E3-4**：**允许**。
- **下一步新会话标题：类案检索助手 E3-4 消费边界与护栏守门**
