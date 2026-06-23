# E7-4 CaseFolder 共享与协作权限 — 执行结论

- 产物时间戳：`20260622-131903`
- 结论：**CONDITIONAL_GO**，`allow_enter_e7_5=true`
- 条件项（环境取证限制，非业务缺陷）：
  1. 后端 pytest 须 host `.venv311`（Py3.11.9）权威复跑收口（VM 沙箱无 `pydantic`/`sqlmodel`/`fastapi` 且无网络，同 E7-1~E7-3 口径）。
  2. 前端 vitest/build 须 host 复跑：本会话 VM bind-mount page-cache 硬钉了 4 个前端文件的旧 inode（`casebookApi.ts`/`CasebookPage.tsx` + 两测试），esbuild/vitest 读到截断旧版。host Read 已逐文件确认实盘完整、闭合、正确。

## 新增 / 修改文件

后端：
- `apps/api/app/casebook/schemas.py`：新增 `CaseFolderShareRequest`（`visibility: Literal["private","team"]` + 可选 `team_id`，`extra="forbid"`）。
- `apps/api/app/casebook/store.py`：新增 `set_sharing()`（owner 取行校验 + 原子改 `visibility`/`team_id`，满足隔离不变式）。
- `apps/api/app/casebook/service.py`：新增 `share_case_folder()`（调 `set_sharing`，出库经 `_row_to_folder`→`sanitize_case_folder` 双保险）。
- `apps/api/app/casebook/router.py`：新增 `POST /folders/{id}/share` 端点 + `_resolve_team_service()`/`_resolve_read_ctx()` 助手；`GET /folders` 与 `GET /folders/{id}` 加 `X-Team-Id` 头进团队态；`PUT` 加 `ValueError` 护栏（单用户态请求 team 归一 400）。
- `apps/api/app/casebook/__init__.py`：导出 `CaseFolderShareRequest`。
- `apps/api/tests/test_e7_casebook_sharing.py`：新增（共享切换 / 鉴权矩阵 / public 拒 / 降级 / 不放开正文 / 审计脱敏）。

前端：
- `apps/web/src/services/casebookApi.ts`：新增 `shareCaseFolder` / `toCaseFolderShareBody` / `CaseFolderShareInput` / `CaseFolderVisibility`；`listCaseFolders`/`getCaseFolder` 加 `teamId` 选项 + `X-Team-Id` 头。
- `apps/web/src/pages/CasebookPage.tsx`：新增 owner-only `ShareControl`（private↔team 切换）+ `TeamContextBar`（团队态列出）；非 owner 只读、无控件。
- `apps/web/src/services/casebookApi.test.ts`：新增 share 体 + fetch wiring 测试。
- `apps/web/src/pages/CasebookPage.test.tsx`：原「E7-4 留」断言改为 owner 共享切换 + 非 owner 只读。

## 共享语义与端点形态

- 共享 = `private → team`：要求 owner + 该 `team_id` 活跃成员（经 M5 `TeamService.resolve_tenant` 校验），原子写 `team_id=team_id` + `visibility=team`。
- 取消共享 = `team → private`：owner 操作，原子写 `team_id=None` + `visibility=private`（回 owner 私有不变式）。
- 端点 `POST /api/casebook/folders/{id}/share` 挂在 `casebook_router` 下，**不单独 include**，`include_router` 仍 16。
- 读取团队共享：`GET /folders`、`GET /folders/{id}` 接受可选 `X-Team-Id` 头，经 `resolve_tenant` 进团队态；非成员降级单用户私有（绝不串读他团队）。
- 可见性只 `private|team` 两级；`public` 或非法值在 schema `Literal` 层即 422。

## 对象级鉴权矩阵（复用 M5）

| 场景 | owner | 同 team 成员 | 非成员 / 跨租户 |
| --- | --- | --- | --- |
| private folder | 读写 | 404 | 404 |
| team folder | 读写 | 只读（带 `X-Team-Id`） | 404（伪造头经 resolve_tenant 降级私有 → 取不到） |
| 改 visibility / 写 | 仅 owner | 404 | 404 |

不泄露他人 folder 存在性：越权一律 404，不区分「不存在」与「无权」。

## 复用 M5 机制的证明

- 隔离：`store._tenant_clause` 与 M5 `tenant_visibility_clause` 同构（`own_private OR (team_id==ctx.team_id AND visibility==team)`），无第二套权限模型。
- 成员关系：经 `app.api.team._get_service()` 取**同一** `TeamService` 实例 + 同一成员账本（懒导入，与 `_resolve_login` 懒导入 auth 同范式）；`resolve_tenant` 非成员降级私有。
- owner 校验：`set_sharing`/`update_owned` 均按 `owner_user_id` 取行，非 owner 取不到 → `None` → 404。
- 默认私有：`create` 恒 `private`；team 仅经显式 `/share`。

## 共享不放开正文证明

- `CaseFolderShareRequest` `extra="forbid"` 且字段仅 `visibility` + `team_id`：夹带 `candidate_refs`/`judgment_text` 等键即 422（测试覆盖）。
- `set_sharing` 只写 `visibility`/`team_id` 两列，不触碰 `search_profile_summary`/`candidate_refs`/`draft_descriptors`。
- 出库 `_row_to_folder` → `sanitize_case_folder` 双保险：共享后读取仍零正文、引用 100% 带锚点（测试 `test_share_does_not_open_body`）。
- 审计日志只记 `user_id_hash` / `case_folder_id_hash` / `visibility` / `has_team` 布尔；不记 note 全文 / team_id 明文 / case_folder_id 明文（测试 `test_share_log_metadata_only`）。

## 隐私扫描

裁判正文 0 / 起草正文 0 / 原始案情 0 / 胜负结论 0 / 凭据 0 / 禁用文案 0（share 路径结构性零正文）。

## include_router

16（未变；share 端点挂 `casebook_router` 下）。

## host 复核命令

```bash
cd apps/api
export DATABASE_URL="sqlite:///./test_e7.db"
pytest tests/test_e7_casebook_sharing.py tests/test_e7_casebook_api.py tests/test_e7_casebook_contracts.py
pytest tests/test_e6_drafting_api.py tests/test_e5_statute_api.py tests/test_e4_intake_api.py
pytest tests/test_search_api_fallback_smoke.py tests/test_feature_flag_rollback.py tests/test_health.py
grep -cE "app\.include_router" apps/api/app/main.py   # 16
cd ../web && npx vitest run src/pages/CasebookPage.test.tsx src/services/casebookApi.test.ts && npx vite build
```

## 是否允许进入 E7-5

是（`allow_enter_e7_5=true`），条件为 host 复核两条（后端 pytest + 前端 vitest/build）通过收口。

下一步：类案检索助手 E7-5 消费边界与护栏守门。
