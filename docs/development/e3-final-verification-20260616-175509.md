# E3 后端 pytest 补验收 · full GO 记录

- 步骤：E3-5 补验收（进入 E4 前唯一前置）
- 时间：2026-06-16T17:55:09+08:00
- 上一版门禁：`docs/development/e3-release-gate-20260616-021500.json`，`overall=CONDITIONAL_GO`
- 本次判定：**GO**
- 范围：只重跑设计文档 18 §9 要求的两条后端 pytest 命令，不改业务代码。

## 1. 前置阻塞复核

上一版 E3-5 的唯一 conditional 原因是当时执行环境无法安装/使用后端依赖，导致两条后端 pytest 不能在该会话重跑。本次使用当前工作区可用的 `.venv311`：

| 项 | 结果 |
|---|---|
| Python | 3.11.9 |
| pytest | 8.2.0 |
| pydantic | 2.7.1 |
| pydantic_core | 2.18.2 |
| 基础外网探测 | `curl https://www.baidu.com/` 返回 200；`Test-NetConnection pypi.org:443` 为 true |
| pypi HTTPS 探测 | PowerShell/curl 仍不稳定，未作为本次判定依据 |

结论：导致上一版 conditional 的核心问题（后端 pytest 无法实际重跑）已解除。

## 2. 设计文档 18 §9 后端 pytest 重跑

执行目录：`apps/api`

### 2.1 E3 专项套件

```bash
..\..\.venv311\Scripts\python.exe -m pytest tests/test_e3_internal_search_contracts.py tests/test_e3_internal_search_service.py tests/test_e3_search_api_parity.py tests/test_e3_internal_search_boundaries.py
```

结果：**86 passed, 1 warning in 4.73s**

### 2.2 后端回归套件

```bash
..\..\.venv311\Scripts\python.exe -m pytest tests/test_m1_3_candidate_comparison.py tests/test_m1_3_legal_candidate_robustness.py tests/test_feature_flag_rollback.py tests/test_health.py tests/test_search_api_fallback_smoke.py tests/test_summary_service.py tests/test_performance_smoke.py tests/test_e1_contracts.py tests/test_e2a_kernel_boundary.py tests/test_e2b_shim_equivalence.py
```

结果：**143 passed, 1 warning in 8.12s**

两条命令均 exit 0，无失败、无跳过。唯一 warning 为 `starlette.formparsers` 对 `python_multipart` 导入方式的 PendingDeprecationWarning，非 E3 行为失败。

## 3. 与旧计数口径的差异

上一版报告中写的 `169 + 60 / 204 passed` 是 E3-4/E3-3 已录得的历史口径。本次按设计文档 18 §9 当前命令逐字执行，当前仓库实际收集数量为 `86 + 143`。差异属于测试文件组合/用例数口径变化；本次验收以“§9 两条命令实际全绿”为准。

## 4. 综合判定

| 门禁 | 结论 |
|---|---|
| E3-0 至 E3-4 既有门禁 | 沿用上一版记录，均 GO |
| 后端 pytest 补验收 | GO，`86 + 143 passed` |
| regression_gate | GO |
| overall | **GO** |
| 是否允许进入 E4 | **允许** |

## 5. 下一步

可以进入 **类案检索助手 E4-1 案情录入端入口合同与本地 only 边界**。

