# M5-3 团队空间与数据隔离 验收报告

- 步骤：M5-3（team_workspace_isolation）
- 时间：2026-06-14 05:59:55
- 结论：**GO**
- 前置依赖：M5-1 入口合同 GO、M5-2 账号体系 GO；落地 M4-7 评估中 `team_workspace_isolation: not_ready`。

## 1. 目标与基调

引入 `team_id / workspace_id`，首次新增服务端沉淀对象持久层（收藏 / 清单 / 报告引用），
并按团队（或单用户 owner）做**行级强隔离**：跨团队、跨用户默认不可见。

遵循「先合同 + 骨架，敏感项谨慎落地」：

- 隔离默认单租户私有（`single_tenant_private`）：`team_id` 为空时等同当前单用户私有行为。
- 查询层强制带租户过滤；隔离不彻底（团队间数据串读）即 NO_GO。
- 持久层只存元数据 / 引用 / 结构化关系字段，绝不存正文。
- `ENABLE_TEAM_WORKSPACE` 默认 false，关闭后回到 M5-2 / M4 末态。

## 2. 数据结构

新增 4 张白名单表（仅结构化字段，无正文列）：

| 表 | 关键字段 |
| --- | --- |
| `m5_team` | team_id / team_name（自填短字段）/ status / created_at / updated_at / reason_code |
| `m5_workspace` | workspace_id / team_id / workspace_name / status / created_at / reason_code |
| `m5_team_membership` | membership_id / team_id / workspace_id / member_user_id / status / created_at / reason_code |
| `m5_sedimentation_object` | object_id / object_type / **owner_user_id / team_id / workspace_id / visibility** / case_id / case_number / court / trial_level / case_cause / judgment_date / source_anchors(JSON 引用) / note / tag / label / list_id / list_title / report_id / status / reason_code / 时间戳 |

- 租户隔离字段：`owner_user_id` / `team_id` / `workspace_id` / `visibility`（private / team）。
- `source_anchors` 仅以 JSON 字符串存来源锚点引用 `[{case_id, source_chunk_id, ...}]`，无正文。

## 3. 隔离规则（核心：跨租户串读 = 否）

强制过滤点：`app.team.isolation.tenant_visibility_clause`，store 层**不提供无过滤读取路径**
（仅 `list_visible` / `get_visible` 两个入口，二者都强制拼接该条件）。

- 单用户私有态（`ctx.team_id is None`）：
  `owner_user_id == ctx.owner_user_id AND team_id IS NULL` —— 只看自己的私有行。
- 团队态（`ctx.team_id` 给定）：
  `(自己的私有行) OR (team_id == ctx.team_id AND visibility == 'team')`
  —— 跨团队（不同 team_id）不可见；他人 private 行不可见。
- 非成员越权传他团队 `team_id` -> 降级单用户私有（`reason_code=not_a_member`），绝不读他团队数据。
- 写入一致性 `assert_write_within_tenant`：写入 team_id 必须等于上下文 team_id；
  `team` 可见性只能在团队态使用；单用户私有态只能写 private 行。

## 4. 兼容与降级

- flag `ENABLE_TEAM_WORKSPACE` 默认 false：所有 `/api/team/*` 返回 403 `TEAM_WORKSPACE_DISABLED`，
  懒初始化零副作用、不建表，回到 M5-2 / M4 单用户私有末态。
- 前端 `TeamWorkspacePanel` 关闭态渲染 null、不调用任何团队接口。
- 团队端点均需登录（复用 M5-2 会话）；账号体系未开启时会话视为无效。

## 5. 隐私规则

- 持久层只存元数据 / 引用 / 结构化关系字段，不存正文。
- 日志只记录 `user_id_hash` / `team_id_hash` / `count` / `status` / `reason_code`；
  不记 login_name / 正文 / 凭据。
- 对外视图里 owner / team 标识以哈希呈现（`uidh_` / `tidh_`）。

## 6. 验证

| 项目 | 命令 | 结果 |
| --- | --- | --- |
| API focused + 门禁 | test_m5_team_isolation + test_health + fallback_smoke + feature_flag_rollback + performance_smoke | **36 passed** |
| API 回归 | test_m5_account_system + commercial_entry_contract + m4_workflow_entry_contract + m4_team_reuse + release_gate | **62 passed** |
| Web 测试 | vitest（分批隔离运行） | **242 passed**（235 基线 + 7 新增）；55 失败仅出现在 `--singleFork`（跨文件状态串扰），默认隔离池下全过 |
| Web 类型检查 | tsc --noEmit | RC=0 |
| Web 构建 | tsc -b && vite build | 118 modules，RC=0（挂载 dist 删除 EPERM，已用新 outDir 验证编译通过） |
| 隐私扫描 | grep | 无正文 / 凭据落库；禁用键只出现在拒绝守卫；两条读路径都强制租户过滤；flag 全默认 false |

新增 16 个 M5-3 后端测试，核心断言：
`test_cross_team_read_is_no` / `test_cross_user_private_read_is_no` /
`test_non_member_team_id_downgrades_to_private` / `test_get_visible_cross_tenant_returns_none` /
`test_api_cross_team_isolation_end_to_end` —— 跨租户串读全部为否。

## 7. 验收标准对照

- [x] 服务端沉淀对象按团队 / 用户强隔离，跨租户不可见。
- [x] `team_id` 为空时行为与单用户私有一致。
- [x] 关闭 flag 回到 M4 末态。
- [x] 隔离不改变主排序 / 召回 / source selection。
- [x] 无正文泄露、无跨租户串读。

## 8. 止损（NO_GO）检查

跨团队 / 跨用户串读、隔离可绕过、正文落入团队持久层、flag 默认开启、隔离改变既有默认行为——
**均未触发**。

## 9. 结论与下一步

M5-3 **GO**。下一步：M5-4 权限分级与对象级访问控制（permission_tiering）。
