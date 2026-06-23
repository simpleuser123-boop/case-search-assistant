# E-2b 共享内核物理迁移 · 验收报告

- 步骤：E-2b（E 系列多产品生态 · 共享内核抽取之物理迁移步）
- 时间：2026-06-15T11:35:52
- 比对基线：E-2a 末态（`docs/development/e2a-release-gate-20260615-095700.json`）
- 结论：**GO** — 后端物理迁移全部达标且行为零变化；前端 build 经修复后回到 E-2a 末态 118 modules、35 个测试文件全通过。E-2「共享内核抽取」整体收官。

---

## 1. 入场证据复核

- E-2a 行为零变化 = GO（gate overall=GO，三结论全 GO）。
- E-1 三类结论仍 GO；`ENABLE_WEIGHTED_RERANK` + 6 个 M4 + 7 个 M5 + 5 个 E-1 产品 flag（含 VITE 镜像）默认全 false。
- 预迁移后端回归基线 91 passed（84 E-1 同款 + 7 E-2a 边界），与 E-2a 末态一致。

## 2. 环境约束（如实记录）

- 本仓工作副本**无 `.git`**（历次 E 步基线均为 JSON gate，非 git commit）。故 `git mv` 环境不可用，**git 历史保留在环境层面不可能**——这是环境限制，非 E-2b 自身违规。
- 挂载盘初始**拒绝 `mv` / `rm`**（Operation not permitted）；经 `allow_cowork_file_delete` 授权后可删除，用于清理探针与被遮蔽的旧 surface 文件。
- 挂载盘存在 **flush 截断 bug**（E-2a 已记录其击中 `search.py`）。本步亦命中：写 `test_e2b_shim_equivalence.py` 时 Edit 工具落盘被截断，改用 bash heredoc 整文件重写恢复（与 E-2a 同款 recipe）。

## 3. 物理迁移实现

因 `git mv`/`mv` 不可用，物理迁移以**等价四步**实现：

1. 复制四组 11 包真实实现进 `app/kernel/<group>/<pkg>/`。
2. 重写迁入文件内部 import：所有 `app.<pkg>` → `app.kernel.<group>.<pkg>`（迁入区残留旧前缀 = 0）。
3. 旧路径原地改为 identity-preserving shim（见 §4）。
4. 删除被包目录遮蔽的旧 surface 文件 `kernel/{rag,identity,guardrails,data}.py`（包目录优先级高于同名 .py，已实测验证）。

四组成员归属（文档 17 §2.1）：

- `kernel/rag/`：retrieval · rerank · query_processing · summary
- `kernel/identity/`：account · team · permission · sharing
- `kernel/guardrails/`：contracts（+ 锚点校验/多租户过滤/对象级鉴权 共享自 identity 三包）
- `kernel/data/`：pipeline · case_store

**内容零漂移证明**：56 个迁入文件与预迁移原件（`/tmp/premig` 备份解包）做「归一化所有 `app.*` 内核包路径后 diff」，逐一 **0 行漂移**——证明仅 import 路径改写，无任何被调逻辑/签名/常量/正文改动。

## 4. 旧路径 shim（零行为分叉）

- 11 个旧包**全部保留**为 shim，禁止「移走即删旧路径」造成断引用。
- `__init__.py`：re-export 形式（importlib 转发新位置全部公开符号 + `__all__`）。
- 各子模块 `.py`：self-replacing 形式（`sys.modules[__name__] = import_module(新位置)`），保证**旧子模块路径与新位置为同一模块对象**。
- 断引用自检：`import app.retrieval, app.rerank, app.query_processing, app.summary, app.account, app.team, app.permission, app.sharing, app.contracts, app.pipeline, app.case_store` → `shim ok`。
- 消费方（api/ eval/ main.py bulk_import/）全部经 shim 正常 import。

## 5. 测试结果

| 项 | 结果 | 说明 |
|---|---|---|
| E-2a 同款回归集 | **91 passed** | 与 E-2a 末态逐位一致 |
| shim 等价性（新增） | **49 passed** | old is new：模块对象 + 公开符号 + 内核公开面抽样 |
| import 边界守门 | **7 passed** | 仅放宽公开面文件存在性断言（接受包形态），依赖方向规则逐字未改 |
| 回归集+shim 合计 | **140 passed** | = 91 + 49，零新增失败 |
| 全量后端 suite | **545 passed / 2 failed** | 545 = E-2a 末态 496 + 新增 shim 49 |

**2 条失败为环境既有、与 E-2b 无关**：`test_day1_api_skeleton` 两条用例在 `/tmp/premig` 预迁移备份上以**完全相同**的 `AssertionError: assert 1 == 2` 失败；不在 E-2a 认证回归集（91）内。

## 6. 硬门禁复核

- 旧路径全留 shim、无断引用、shim 与新位置同一对象（无行为分叉）。✅
- 迁移未改被调逻辑/签名/主排序/source selection/rerank 默认（0 行内容漂移）。✅
- 未建产品包（intake/statute/drafting/casebook 均 absent）；内核不反向 import 产品包（0 命中）。✅
- 未暴露检索内部服务接口（E-3 范畴）；未注册新端点（10 router 不变）；未加前端产品入口。✅
- 全部后端 flag 默认 false（13 项逐一核验 config.py）。✅
- 无 qrels/label/relevance 进运行时；无 query id/case id 特判。✅
- 扫描：正文 0 / 凭据 0 / 禁用文案 0 命中（kernel 内命中均为 `FORBIDDEN_BODY_KEYS` 护栏定义与 no-body 文档串，及既有 LLM 调用逐字迁移）。✅

## 7. 前端 build（首跑被既有损坏阻塞，已修复）

首跑时 `tsc -b` 失败：**14 个前端源/测试文件在磁盘上被截断**（既有 mount flush 截断 bug）：

```
src/vite-env.d.ts                              src/config/featureFlags.ts
src/components/bulkImport/BulkImportPanel.tsx  src/components/bulkImport/BulkImportPanel.test.tsx
src/components/permission/PermissionPanel.tsx  src/components/permission/PermissionPanel.test.tsx
src/components/sharing/SharingPanel.tsx        src/components/sharing/SharingPanel.test.tsx
src/components/team/TeamWorkspacePanel.tsx     src/components/team/TeamWorkspacePanel.test.tsx
src/pages/HomePage.tsx                         src/pages/HomePage.test.tsx
src/pages/SearchPage.test.tsx                  src/services/billingApi.ts
src/services/tendencyApi.ts
```

判定为**既有损坏、非 E-2b 引入**：E-2b 为后端 `apps/api` 单边迁移，未触碰任何前端文件；全部文件 mtime 早于本会话起始；截断在磁盘上稳定（多次/多法读取字节数一致）。

**修复经过**：用户在 Windows 侧用 codex 补全这些文件。但 host→VM 增量同步只在**内容写入事件**时推送——改已有文件（含 PowerShell 改 mtime）不触发同步，故首两轮文件未传进 VM。最终用户用 `Compress-Archive` 把 `src` 打包为**新文件** `src_fixed.zip`（新文件可同步），VM 侧解压覆盖 `src/`。

**修复后结果**：`tsc -b` 0 error，`vite build` **118 modules transformed**（与 E-2a 末态一致），**35 个前端测试文件全部通过**（≈283 用例，0 失败）。所有 flag 仍默认 false。

## 8. 回退方案

后端可回退：删除 `app/kernel/{rag,identity,guardrails,data}/`，用 `outputs/e2b-backup/app-tests-backup-20260615-104530.tgz` 还原 `app/` 与 `tests/` 即回到 E-2a 末态。

## 9. 产物

- `docs/development/e2b-kernel-migration-20260615-113552.md`（本报告）
- `docs/development/e2b-release-gate-20260615-113552.json`
- `docs/development/e2b-parameter-version-20260615-113552.json`

## 10. 结论与下一步

E-2b 共享内核物理迁移 **GO**：后端行为零变化（56 文件 0 行内容漂移、shim 旧符号 is 新符号、回归 91 + shim 49 = 140 passed、硬门禁与扫描全过）；前端经修复后 build 回到 118 modules、35 个测试文件全通过。**E-2「共享内核抽取」整体收官。**

**下一步 E-3**：检索内部服务接口（检索助手对内暴露检索服务，输入 SearchProfile / 输出 CandidateRef，护栏在检索侧统一执行）。本 e2b-release-gate 即 E-3 比对基线（E-2 末态），无前置阻塞。
