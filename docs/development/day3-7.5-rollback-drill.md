# Day 3 7.5 回滚演练记录

日期：2026-06-08

范围：仅演练四个回滚开关。不对外发布，不新增排序策略，不重建索引，不修改语料，不做收藏、导出或类案报告。

## 1. 结论

7.5 回滚门禁：Go。

四个开关均已通过自动化演练，最大恢复耗时 23ms，均记录 `rollback_event`，日志不包含原始 query，不需要重建向量索引。当前源码默认关闭态的真实 `/api/search` 也可返回基础搜索结果。

MVP 内测/下一阶段结论：有条件 Go，仅限本机/内部基础搜索内测，且保持四个增强开关关闭。不建议对外灰度或启用新排序。原因是 7.2 仍为 `PARTIAL_BLOCKED`，`enable_new_rerank=false`；7.3 仍缺少持久化性能结果。

## 2. 自动化演练

命令：

```powershell
cd apps/api
python scripts/day3_7_5_rollback_drill.py --out ..\..\docs\development\day3-7.5-rollback-drill.json
```

结果文件：`docs/development/day3-7.5-rollback-drill.json`

| 开关 | 端点 | 状态 | 耗时 | 关键验证 |
| --- | --- | --- | --- | --- |
| `ENABLE_QUERY_REWRITE=false` | `/api/search` | passed | 23ms | `QUERY_REWRITE_DISABLED`；仅原始 query plan 进入检索；结果可返回。 |
| `ENABLE_WEIGHTED_RERANK=false` | `/api/search` | passed | 11ms | `score_mode=base_retrieval`；`final_score=retrieval_score`。 |
| `ENABLE_SUMMARY=false` | `/api/search` | passed | 12ms | `SUMMARY_DISABLED`；`summary.method=source_snippet`；未调用摘要 LLM。 |
| `ENABLE_EXPANDED_SEARCH=false` | `/api/search/expand` | passed | 8ms | 返回 `403 EXPANDED_SEARCH_DISABLED`；未调用检索服务。 |

自动化范围说明：脚本使用 in-process FastAPI 与 mock 外部依赖，验证开关行为和脱敏日志，不访问 DeepSeek、Ollama、Chroma，不修改 `.env`，不触碰 `data` 或 Chroma 持久化目录。

## 3. 真实依赖补充验证

临时启动当前源码 API 后，`/health` 返回：

```text
feature_flags: ENABLE_QUERY_REWRITE=false, ENABLE_WEIGHTED_RERANK=false, ENABLE_SUMMARY=false, ENABLE_EXPANDED_SEARCH=false
ollama_reachable=true
chroma_collection_queryable=true
chroma_chunk_count=9621
db_reachable=false
```

真实 `/api/search` 默认关闭态验证：

```text
status_code=200
result_count=3
degraded_reasons=QUERY_REWRITE_DISABLED,SUMMARY_DISABLED
first_score_mode=base_retrieval
first_summary_method=source_snippet
total_duration_ms=2846
rebuild_index_required=false
```

说明：运行中的 `127.0.0.1:8000` 曾返回缺少 `feature_flags` 的旧健康检查结构，判断为旧 API 进程。实际本机演练必须重启 API 进程后再验收当前源码。

## 4. 本机手工演练步骤

通用步骤：

1. 记录当前状态：`GET /health`，确认 `feature_flags` 和依赖状态。
2. 临时修改环境变量或测试配置。
3. 重启 API；若涉及前端入口，重启 Web dev server 或重新 build。
4. 执行 `/api/search`、`/api/search/expand` 或前端主链路。
5. 验证响应/页面符合关闭态行为。
6. 恢复原配置并重启必要服务。

`ENABLE_QUERY_REWRITE=false`

```powershell
cd apps/api
$env:ENABLE_QUERY_REWRITE = "false"
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

验证：`/api/search` 返回 200，`degraded_reasons` 包含 `QUERY_REWRITE_DISABLED`，日志有 `rollback_event` 和 `input_hash`，不包含原始 query。

恢复：停止 API，删除临时变量或改回原值，重启 API。

`ENABLE_WEIGHTED_RERANK=false`

```powershell
cd apps/api
$env:ENABLE_WEIGHTED_RERANK = "false"
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

验证：`/api/search` 返回 200，结果项 `score_breakdown.score_mode=base_retrieval`，`weighted_rerank_enabled=false`，前端使用基础分数仍可展示相似度。

恢复：停止 API，删除临时变量或改回原值，重启 API。

`ENABLE_SUMMARY=false`

```powershell
cd apps/api
$env:ENABLE_SUMMARY = "false"
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

验证：`/api/search` 返回 200，`degraded_reasons` 包含 `SUMMARY_DISABLED`，结果项 `summary.method=source_snippet` 且有 `source_chunk_id`；前端显示来源片段，不展示无来源生成摘要。

恢复：停止 API，删除临时变量或改回原值，重启 API。

`ENABLE_EXPANDED_SEARCH=false`

```powershell
cd apps/api
$env:ENABLE_EXPANDED_SEARCH = "false"
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

后端验证：`POST /api/search/expand` 返回 `403`，错误码为 `EXPANDED_SEARCH_DISABLED`，日志有 `rollback_event`，检索服务不执行。

前端入口同步：

```powershell
cd apps/web
$env:VITE_ENABLE_EXPANDED_SEARCH = "false"
npm run dev
```

前端验证：搜索结果少于 5 条或无结果时，不显示“查看可能相关候选”或“使用扩展检索”，页面仍显示主结果、来源片段和降级提示。

恢复：停止 API/Web，删除临时变量或改回原值，重启服务。

## 5. 验证命令

```powershell
cd apps/api
pytest
```

结果：108 passed。

```powershell
cd apps/web
npm test
```

结果：39 passed。

```powershell
cd apps/web
npm run build
```

结果：通过，Vite production build 成功。

页面级说明：未发现可用的 in-app Browser 控制工具；前端回滚展示由 `SearchPage.test.tsx` 覆盖，构建已通过。现有 Day 3 7.1 CDP smoke 脚本仍保留，可在需要真实浏览器联调时单独运行。

## 6. 未验证项与阻塞

- 对外灰度未执行，符合本轮限制。
- `127.0.0.1:8000` 旧进程需要重启后才可看到当前源码的 `feature_flags`。
- PostgreSQL 当前 `db_reachable=false`，但搜索、回滚和 Chroma/Ollama 检索链路不依赖该 DB，本轮不修复。
- 7.2 当前为 `PARTIAL_BLOCKED`，不能启用 `ENABLE_WEIGHTED_RERANK=true` 做灰度。
- 7.3 缺少持久化 P95/P99 性能结果，不能证明增强能力适合外部灰度。

## 7. Day 3 Go/No-Go

7.5 回滚演练：Go，完成。

Day 3 进入内部基础搜索内测：有条件 Go，要求四个增强开关保持关闭，并先重启当前源码 API/Web。

Day 3 对外灰度或进入增强能力下一阶段：No-Go，直到 7.2 评测和 7.3 性能证据闭环。
