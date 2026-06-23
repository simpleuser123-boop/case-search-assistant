# E7-4 CaseFolder 共享与协作权限 - Host 复核收口

- 复核时间：`20260623-101905`
- 环境：host `python`（Python 3.11.9）, Windows PowerShell
- 结论：**GO（E7-4 host 复核已收口）**
- 关联 gate：`docs/development/e7-casebook-sharing-20260622-131903.json` / `docs/development/e7-casebook-sharing-20260622-131903.md`

## 复核范围

按 E7-4 gate `host_reverify_commands` 原样等价执行：

1. 后端 E7-4 共享/API/contract：
   - `python -m pytest tests/test_e7_casebook_sharing.py tests/test_e7_casebook_api.py tests/test_e7_casebook_contracts.py`
2. E6/E5/E4 回归：
   - `python -m pytest tests/test_e6_drafting_api.py tests/test_e5_statute_api.py tests/test_e4_intake_api.py`
3. 基础 smoke / flag / health：
   - `python -m pytest tests/test_search_api_fallback_smoke.py tests/test_feature_flag_rollback.py tests/test_health.py`
4. `include_router` 计数：
   - `Select-String app/main.py "app\.include_router"` -> `16`
5. 前端 host 验收：
   - `npx vitest run src/pages/CasebookPage.test.tsx src/services/casebookApi.test.ts`
   - `npx vite build`

## 结果

- 后端 E7-4 共享/API/contract：**100 passed**
- E6/E5/E4 回归：**74 passed**
- 基础 smoke / flag / health：**18 passed**
- `include_router`：**16**
- 前端 vitest：**30 passed**
- 前端 `vite build`：**passed**

## 本次收口中修正的仅测试问题

本次 host 复核发现并修正 2 个测试断言误伤，均非业务缺陷：

1. `apps/api/tests/test_e7_casebook_sharing.py`
   - 失败原因：`caplog.records` 混入 `httpx` TestClient 请求日志，URL 路径自带明文 `folder_id`，误伤了“审计日志不含明文 id”断言。
   - 修正：断言只采样 `case_search` logger 的 `casebook_share` 审计记录，继续验证审计日志无明文 `team_id` / `folder_id` / `note`。
2. `apps/web/src/pages/CasebookPage.test.tsx`
   - 失败原因：页面中有两处文本包含“只读访问”，`getByText(/只读访问/)` 选择器过宽导致多命中。
   - 修正：改为断言共享控件区域的唯一提示文案 `由协作夹所有者管理共享，你当前为只读访问。`

## 仍存在但不阻塞 gate 的告警

- `starlette.formparsers` 的 `PendingDeprecationWarning: Please use import python_multipart instead.`（pytest warning，非失败）
- `CasebookPage.test.tsx` 存在 React Testing Library `act(...)` warning（vitest warning，测试通过，未阻塞 E7-4 gate）
- `vite build` 有 chunk size > 500 kB 的提示（warning，构建成功）

## 收口判断

- E7-4 原 gate 中的两项条件项均已完成 host 权威复跑：
  - 后端 pytest：已完成并通过
  - 前端 vitest/build：已完成并通过
- 因此 E7-4 可视为 **host 复核已收口**，可作为 E7-5 / E7-6 的已验证输入继续使用。
