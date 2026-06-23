"""M5-4 权限分级与对象级访问控制。

在 M5-3 团队隔离之上引入「角色 + 对象级 ACL」：
- 角色：owner（创建者 / 管理）、editor（可编辑）、viewer（只读）。
- 对象级 ACL：每个沉淀对象的每一次读写都经过对象级鉴权；越权访问被拒绝（403）
  并写入脱敏审计事件。

安全红线（落地基调 / 禁止项）：
- 默认最小权限：未显式授权即只有 owner 可见可改；新成员默认 viewer。
- 不默认授予超出 owner 的访问权限；不默认放宽跨用户可见性。
- 审计只记录脱敏字段（actor hash / object id hash / action / result / reason_code /
  permission_level），绝不落正文 / 凭据。
- 权限不改变主排序 / 召回 / source selection，也不改变 M2/M3/M4 默认行为。
- ENABLE_PERMISSION_TIERING 默认 false：关闭时本模块不建表、不鉴权、不审计，
  行为回到 M5-3 / M4 末态（owner 私有，无角色概念）。
"""
