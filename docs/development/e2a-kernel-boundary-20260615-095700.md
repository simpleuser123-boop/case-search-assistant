# E-2a 共享内核逻辑边界冻结 · 验收报告

- **步骤**：E 系列多产品生态全链路闭环 · E-2a（共享内核抽取「先逻辑后物理」之逻辑步）
- **时间**：2026-06-15T09:57:00
- **性质**：结构重整步——声明内核公开面 + 收敛检索链路引用 + import 边界守门；**零文件移动、零运行时语义变化**
- **比对基线**：`docs/development/e1-release-gate-20260615-075029.json`
- **结论**：**GO**（三类全 GO）

---

## 1. 入场证据复核

E-1 三类全 GO（基础搜索可用 / 入口合同冻结 / 行为与 M5-10 零变化），见 `e1-release-gate-20260615-075029.json`。13 个产品/特性 flag（6 M4 + 7 M5 口径 + 5 E-1 产品 flag 及 VITE 镜像，ENABLE_WEIGHTED_RERANK 在内）改前复核默认全 false。改前复跑 E-1 同款回归集 **84 passed**，与 E-1 末态逐位一致，作为本步基线。

## 2. 共享内核公开面（kernel surface）

新增 `apps/api/app/kernel/`，**纯 re-export 聚合层**，零文件移动：

| 组 | 公开面文件 | 收敛的现有内核成员（文档 17 §2.1） |
| --- | --- | --- |
| RAG 核心 | `kernel/rag.py` | retrieval / rerank / query_processing / summary |
| 身份与租户 | `kernel/identity.py` | account / team / permission / sharing |
| 契约与护栏 | `kernel/guardrails.py` | contracts + 锚点校验(sharing.anchors) + 多租户过滤(team.isolation) + 对象级鉴权(permission.access) |
| 数据 | `kernel/data.py` | pipeline / case_store |

`kernel/__init__.py` 聚合四组并声明 `KERNEL_GROUPS`、`PRODUCT_PACKAGES`（intake/statute/drafting/casebook 命名空间预置）。共 re-export **145** 个稳定符号，运行时验证全部 `is`-identical 于源符号（纯转发、不复制实现、不改签名）。

> 范围说明：billing / bulk_import / tendency_analysis / tendency_gate / eval 按文档 17 §2.1 **不在**本次内核公开面冻结范围。禁用文案守门（tendency_gate FORBIDDEN_PHRASES）属 M5 商业化能力，guardrails 组不导出，留待后续步骤判定归属。

## 3. 检索链路引用收敛

`apps/api/app/api/search.py` 对 RAG 核心组的引用统一改走 `from app.kernel.rag import (...)`；仅 import 来源路径收敛，**调用逻辑、顺序、签名零变化**，模块级单例（retrieval_service / rerank_service / summary_service / query_processing_service）不变。

> 工程记录：编辑 search.py 时触发挂载盘 flush 截断 bug（Edit 工具写入 >21KB 文件时 Linux 挂载侧字节被截断）。改用 bash heredoc 整文件重写恢复（Read 工具 Windows 侧视图为权威完整内容）。**A/B 等价性证明**：把 search.py 换回原始深引 import（去 kernel 公开面）跑全量，行为与公开面版本完全一致——既证明重写后 body 逐位等价，也证明下文 2 条失败用例与 E-2a 无关。

## 4. import 边界守门测试

新增 `apps/api/tests/test_e2a_kernel_boundary.py`（7 用例，**全通过**），用 AST 静态扫描（零运行时副作用、不受 flag 漂移影响）：

1. 内核公开面不反向 import 任何产品包命名空间。
2. 内核成员包不反向 import 任何产品包。
3. 产品包命名空间互不 import（当前不存在 → 「不存在即通过」+ 规则预置）。
4. 检索主链路 search.py 只经 `app.kernel` 公开面消费内核（不深引内部子模块）。
5. 除 grandfather 基线外，api/ 消费方不得新增绕过公开面的内核深引。
6. grandfather 名单文件存在性自检。
7. 内核公开面四组文件齐备。

grandfather 基线 = E-2a 之前已存在内核深引的 8 个 api/ 端点（auth/billing/bulk_import/cases/health/permission/sharing/team）；E-2a 仅收敛 search.py，其余冻结为基线只禁新增，留待 E-2b shim。

## 5. 回归与构建

- **后端**：E-1 同款回归集 **84 passed**（与 E-1 末态一致）+ E-2a 守门 **7 passed** = 91；全量 **496 passed / 2 failed**。
- **2 条失败**（`test_day1_api_skeleton.py` 的 search / search_expand 两用例）经 A/B 测试证明为**环境既有、与 E-2a 无关**，且不在 E-1/M5 认证回归集内（E-1 基线 = 60+24=84，已逐位复现）。
- **前端**：`tsc -b` exit 0；`vite build` **118 modules transformed**（与 E-1 末态一致），bundle 正常渲染。零前端源码改动。

## 6. 硬门禁与扫描

| 门禁 | 结果 |
| --- | --- |
| E-1 三类全 GO | ✅ |
| 零文件物理移动/重命名 | ✅（11 个原内核包原位，kernel/ 纯新增） |
| 无新建产品包 / 无暴露检索服务 / 无新端点 / 无前端入口接线 | ✅ |
| 公开面纯 re-export、检索链路走公开面 | ✅ |
| 所有既有 flag 默认 false（含 ENABLE_WEIGHTED_RERANK） | ✅ |
| 前端 build modules = 118（与 E-1 一致） | ✅ |
| 主排序 / source selection / rerank 默认未变 | ✅ |
| 被调函数运行时语义未变 | ✅ |
| qrels/label/relevance 未进运行时、无 query id/case id 特判 | ✅ |
| 扫描：正文 0 / 凭据 0 / 禁用文案 0 | ✅ |

## 7. 结论与下一步

| 结论维度 | 判定 |
| --- | --- |
| 基础搜索持续可用 | **GO** |
| 内核逻辑边界冻结（纯 re-export） | **GO** |
| 行为与 E-1 末态零变化 | **GO** |
| **总体** | **GO** |

**可回退**：E-2a 为纯增量——删除 `app/kernel/` 与守门测试、把 search.py import 切回原路径即回 E-1 末态。

**下一步 E-2b**（物理迁移）：四组内核 `git mv` 进 `app/kernel/{rag,identity,guardrails,data}/`，旧路径留 re-export shim，引用切到 kernel，加 shim 等价性测试，行为与 E-2a 末态逐位一致。本报告对应 gate 即 E-2b 比对基线。
