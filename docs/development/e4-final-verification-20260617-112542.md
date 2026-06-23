# E4 最终验收与 E5 入口门禁 · full GO 记录

- 步骤：E4-6（E4 验收与下一步门禁）
- 时间：2026-06-17T11:25:42+08:00
- 基线：`docs/development/e3-release-gate-20260616-175509.json`
- 本次判定：**GO**
- 范围：只做收官验证、产物生成和 GO/NO_GO 判定；未改业务代码。

## 1. 前置 conditional 解除

E4-1 与 E4-2 历史报告中的 `CONDITIONAL_GO` 原因均为当时 VM/host 后端 pytest 复跑口径未完全闭合。本次使用 host `.venv311`（Python 3.11.9）按 E4-6 命令实际复跑，E4 全套与 E1~E3 回归均通过，前置 conditional 已解除。

E4-3、E4-4、E4-5 既有报告均为 GO；E4-5 中建议的 host `.venv311` 复跑已在本次完成。

## 2. 验证环境

| 项 | 结果 |
| --- | --- |
| Python | 3.11.9 |
| pytest | 8.2.0 |
| pydantic | 2.7.1 |
| pydantic_core | 2.18.2 |
| DATABASE_URL | `sqlite:///./test_e4.db` |

## 3. 后端验证

执行目录：`apps/api`

### 3.1 E4 全套

```bash
..\..\.venv311\Scripts\python.exe -m pytest tests/test_e4_intake_contracts.py tests/test_e4_intake_sanitize.py tests/test_e4_intake_api.py tests/test_e4_intake_boundaries.py
```

结果：**126 passed, 1 warning in 3.35s**

### 3.2 E1~E3 同款回归

```bash
..\..\.venv311\Scripts\python.exe -m pytest tests/test_e1_contracts.py tests/test_e2a_kernel_boundary.py tests/test_e2b_shim_equivalence.py tests/test_e3_internal_search_contracts.py tests/test_e3_internal_search_service.py tests/test_e3_search_api_parity.py tests/test_e3_internal_search_boundaries.py
```

结果：**172 passed, 1 warning in 2.85s**

计数说明：E4-5 报告写入的是 `169 passed`。当前仓库中 `test_e2a_kernel_boundary.py` 实收 `13 passed`，比旧口径多 3 条 E4 边界规则，因此本次 E4-6 以当前 host 实跑的 `172 passed` 为准。

### 3.3 M1 / feature flag / health / summary / performance smoke

```bash
..\..\.venv311\Scripts\python.exe -m pytest tests/test_m1_3_candidate_comparison.py tests/test_m1_3_legal_candidate_robustness.py tests/test_feature_flag_rollback.py tests/test_health.py tests/test_search_api_fallback_smoke.py tests/test_summary_service.py tests/test_performance_smoke.py
```

结果：**60 passed, 1 warning in 7.36s**

后端唯一 warning 为 `starlette.formparsers` 对 `python_multipart` 导入方式的 `PendingDeprecationWarning`，非 E4 行为失败。

## 4. 前端验证

执行目录：`apps/web`

```bash
npm run test
```

结果：**38 files / 307 tests passed**。存在 `IntakePage.test.tsx` 的 React `act(...)` 测试警告，exit 0，非阻塞。

```bash
npm run build
```

结果：**PASS**，`121 modules transformed`，输出 `dist/index.html`、CSS、JS。

## 5. 静态扫描

| 扫描项 | 结果 |
| --- | --- |
| `include_router` 数 | 13 |
| intake 端点 | 仅 `POST /api/intake/search` |
| statute / drafting / casebook 产品包 | 均不存在 |
| ENABLE_INTAKE / ENABLE_INTAKE_AI_EXTRACTION 默认 | 均为 false |
| VITE_ENABLE_INTAKE / VITE_ENABLE_INTAKE_AI_EXTRACTION 默认 | 均为 false |
| 服务端 AI 增强 on 路径 | 未接线 |
| 检索底层 / 其它产品包引用 | 只出现在禁止性注释或测试说明中；静态守门测试通过 |
| 禁用文案 / 密钥模式 | 未发现真实命中；仅 E4-4 报告中出现“禁用文案 0”的说明行 |

## 6. 验收判定

| 门禁 | 结论 |
| --- | --- |
| entry_gate | GO，E4-1 历史 conditional 已由 host E4 全套复跑解除 |
| sanitize_gate | GO，E4-2 历史 conditional 已由 host E4 全套复跑解除 |
| intake_api_gate | GO |
| frontend_gate | GO |
| boundary_gate | GO |
| regression_gate | GO，后端 `126 + 172 + 60 passed` |
| frontend_parity_gate | GO，前端 `307 passed` + build PASS |
| overall | **GO** |

## 7. 下一步

允许进入 E5。下一步标题：**类案检索助手 E5-1 法条法规检索入口合同**。
