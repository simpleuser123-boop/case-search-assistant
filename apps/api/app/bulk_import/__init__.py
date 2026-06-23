"""M5-6 批量导入包：把既有清单/案例批量导入团队空间。

红线（与 M5-1~M5-5 一脉相承）：
- 导入即元数据/引用 only，绝不引入正文或原始案情。
- 只走白名单字段（M4 元数据全量 + 来源锚点 + 用户自填短字段）；非白名单字段一律丢弃。
- 沿用 case_id + source_chunk_id 做锚点完整性校验；缺锚点项降级或拒绝，绝不伪造锚点。
- 导入对象默认归属当前 owner、默认私有（team_id=None / visibility=private），
  复用 M5-3 SedimentationObject 持久层与白名单清洗；可见性仍由 tenant_visibility_clause 唯一承载。
- 关闭 ENABLE_BULK_IMPORT 时本模块不建表、不导入（由 API 层 flag-gate 保证）。
"""
