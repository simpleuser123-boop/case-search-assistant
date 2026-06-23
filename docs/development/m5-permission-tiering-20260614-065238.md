# M5-4 权限分级与对象级访问控制 — 验收报告

- 时间戳：20260614-065238
- 里程碑步骤：M5-4（permission_tiering），落地 M4-7 评估中 `permission_tiering: not_ready`
- 前置依赖：M5-1 入口合同 / M5-2 账号体系 / M5-3 团队隔离（均已 GO）
- 落地基调：先合同 + 骨架，敏感项谨慎落地；默认最小权限、显式授权、全程审计
- 结论：**GO**

## 1. 目标与红线

在 M5-3 团队隔离之上引入「角色 + 对象级 ACL」，对收藏 / 清单 / 报告等沉淀对象的
每一次读写做对象级鉴权。权限模型错误会导致越权读取他人案件工作产物，故本步以
**默认最小权限、显式授权、全程审计**为红线。

红线落实：

- 默认最小权限：未显式授权即只有 owner 可见可改；非 owner 对 private 对象有效权限为 none。
- 显式授权才扩大可见性：唯一放权入口是对象 owner 创建的 `ObjectGrant`（viewer/editor）。
- 全程审计：授权变更与越权尝试均写脱敏审计（actor hash / object id hash / action /
  result / reason_code / permission_level），绝不落正文 / 凭据 / 原始 object_id 明文。
- 权限不改变主排序 / 召回 / source selection，也不改变 M2/M3/M4 默认行为。
- `ENABLE_PERMISSION_TIERING` 默认 false：关闭时不建表、不鉴权、不审计，回到 M5-3 / M4 末态。

## 2. 角色与权限模型

| 角色 | 等级 | 能力 |
| --- | --- | --- |
| owner | 3 | 读 / 写 / 删 / 授权 / 撤销 / 分配角色（创建者归属，不可被直接授予） |
| editor | 2 | 读 / 写（不能删、不能管理授权） |
| viewer | 1 | 只读 |
| （无） | 0 | 无任何访问权限（默认最小权限兜底） |

动作所需最小等级：read→viewer、write→editor、delete/grant/revoke/assign_role→owner。

**有效权限解析（取各来源最大值）**：

1. 对象 owner（`owner_user_id == actor`）→ owner 级。
2. 该对象的 active 对象级授权（`ObjectGrant`）→ 授予等级。
3. 仅当对象 `visibility==team` 且 actor 是该 team 活跃成员 → 按团队角色折算等级。
   **private 对象绝不因团队成员身份放权——必须显式授权。**
4. 其余 → none（默认最小权限）。

## 3. 鉴权中间件与对象级访问控制

- `app/permission/access.py`：纯逻辑判定中心（`authorize(action, facts)`），不 import
  检索 / rerank / 主排序。
- `app/permission/service.py`：装配「对象事实 + actor 角色 + actor 授权」→ 判定 →
  写审计（allow / deny 均记录）。对 private 对象的显式授权读取经
  `TeamStore.get_object_for_authorization`（取原始行）但**必须 authorize() 通过后才返回**，
  且授权只能由对象 owner 创建，不破坏 M5-3 跨租户隔离。
- `app/api/permission.py`：`/api/permission/{role,grant,revoke,object/read,audit}`，
  flag-gated；关闭态全部 403 `PERMISSION_TIERING_DISABLED`；越权 403 `PERMISSION_DENIED`。
  所有端点需登录（复用 M5-2 会话；账号体系关则会话无效）。

## 4. 审计

- 表 `m5_permission_audit` 列：`audit_id / actor_user_id_hash / object_id_hash /
  action / result / reason_code / permission_level / created_at`。
- 全为脱敏哈希（`uidh_` / `oidh_` 前缀）与短枚举；无正文列、无凭据列、无原始 object_id。
- 记录：授权 grant / revoke、角色 assign_role、对象 read 的 allow / deny，以及
  object_not_found 等 deny 原因。

## 5. 降级与兼容

- `ENABLE_PERMISSION_TIERING` 默认 false（config.py 已于 M5-1 就位）。关闭时：
  懒初始化零副作用（不建表）、所有权限端点 403、前端 `PermissionPanel` 渲染 null、
  `VITE_ENABLE_PERMISSION_TIERING` 默认 false。行为与 M5-3 / M4 末态一致（owner 私有，
  无角色概念）。
- 未触碰 search / cases / rerank / 主排序代码；M2/M3/M4 默认行为不变。

## 6. 新增 / 修改文件

后端（新增包 `app/permission/`）：

- `__init__.py`（16）/ `models.py`（134）/ `access.py`（114）/ `store.py`（202）/
  `service.py`（186）/ `schemas.py`（63）/ `api/permission.py`（216）
- `main.py`：仅新增 include permission_router（一行 import + 一行 include）
- `team/store.py`：新增 `get_object_for_authorization`（受控、仅供对象级鉴权装配事实）
- `tests/test_m5_permission.py`（350，17 项）

前端（新增 `components/permission/`）：

- `services/permissionApi.ts`（132）+ `.test.ts`（5 项）
- `components/permission/PermissionPanel.tsx`（176）+ `.test.tsx`（3 项）
- `config/featureFlags.ts`：新增 `isPermissionTieringEnabled`
- 根 `.env.example`：新增 `VITE_ENABLE_PERMISSION_TIERING=false`

## 7. 验证结果

- 后端 gate（test_health / test_search_api_fallback_smoke / test_feature_flag_rollback）
  + M5-4 focused = **35 passed**；M5-4 focused 单独 **17 passed**。
- 后端回归（M5 team / account / commercial-entry + M4 team-reuse + release-gate）
  = **68 passed**。
- 前端 `vitest run`（27 文件）**246 passed**（含新增 8：permissionApi 5 / PermissionPanel 3）。
- 前端 `tsc --noEmit` RC=0；`vite build` **118 modules transformed**，built green。
- 安全扫描：permission 包源码无正文 / 凭据字段存储；审计表列全为哈希 / 短枚举，
  forbidden-in-audit = NONE；`ENABLE_PERMISSION_TIERING` 默认 False。

### 越权被拒断言（focused 重点）

- 非 owner 对 private 对象（无授权）读 / 写 / 删全被拒（默认最小权限）。
- 越权读他人对象 → 403 + 写 deny 审计（object_id_hash 命中）。
- grantee（editor）非 owner → 不能再对外授权（防权限提升）。
- private 对象不因团队 editor 身份获得读权限（团队角色只对 team 可见对象生效）。
- 撤销授权后再读 → 再次被拒。

## 8. 止损（NO_GO）核对 — 均未触发

- 越权读 / 写未被拦截：否（focused 多用例证明被拒 + 审计）。
- 默认授予过宽权限：否（默认 none，显式授权才放权）。
- 审计含正文 / 凭据：否（列全为哈希 / 短枚举，扫描无命中）。
- `ENABLE_PERMISSION_TIERING` 默认开启：否（默认 false，关闭回 M5-3/M4 末态）。
- 权限改变既有默认行为 / 主排序：否（未触碰检索 / rerank / 排序）。

**结论：GO，可继续 M5-5（沉淀同步与团队共享 list_sharing）。**
