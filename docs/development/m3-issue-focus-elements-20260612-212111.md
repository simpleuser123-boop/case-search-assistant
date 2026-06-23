# M3-3 争议焦点与关键要素提炼验收

生成时间：2026-06-12T21:21:11+08:00

## 门禁

| 项目 | 产物 | 状态 |
| --- | --- | --- |
| M3-1 阅读入口合同 | `docs/development/m3-reading-entry-contract-20260612-194511.json` | GO |
| M3-2 裁判要旨摘要来源闭环 | `docs/development/m3-holding-summary-closure-20260612-202913.json` | GO |
| M2 风险提示 | `docs/development/m2-risk-hints-20260612-181106.json` | GO |
| `ENABLE_WEIGHTED_RERANK` | `apps/api/app/core/config.py`, `.env.example` | false |

## 实现范围

- 后端详情响应新增 `issue_focus` 与 `key_elements`。
- 条目字段限定为 `label`、`category`、`source_anchors`、`confidence`、`degrade_reason`。
- 可展示类别限定为争议焦点、裁判理由中的关键事实、法院认定的关键要素、与用户阅读相关的程序或证据节点。
- 前端详情页新增阅读导航区块，只作为复核线索和阅读定位，不替代 M2 风险提示。
- 客户端再次校验 `case_id`、`source_chunk_id` 与详情来源片段一致后才展示。
- 无锚点、来源不一致、来源不足或生成失败均降级为空状态或来源入口。

## 边界确认

| 边界 | 结果 |
| --- | --- |
| 改变搜索排序 | 否 |
| 改变候选召回 | 否 |
| 改变 source selection | 否 |
| 改变 rerank 默认开关 | 否 |
| 改变 M2 扩展检索默认行为 | 否 |
| 使用 qrels / label / relevance | 否 |
| 使用 query id / case id 特判质量 | 否 |
| 实现 M3-4 及之后能力 | 否 |
| 报告或 JSON 写入正文内容 | 否 |
| 日志写入焦点、要素或来源正文 | 否 |

## 修改文件

- `apps/api/app/schemas.py`
- `apps/api/app/summary/service.py`
- `apps/api/app/summary/__init__.py`
- `apps/api/app/api/cases.py`
- `apps/api/tests/test_m3_issue_focus.py`
- `apps/web/src/types/search.ts`
- `apps/web/src/components/details/CaseDetailDrawer.tsx`
- `apps/web/src/pages/SearchPage.test.tsx`
- `apps/web/src/mocks/caseDetailMockFixture.ts`

## 验证

| 命令 | 结果 |
| --- | --- |
| `cd apps/api; pytest tests/test_m3_issue_focus.py tests/test_m3_holding_summary.py` | 11 passed |
| `cd apps/api; pytest tests/test_summary_service.py tests/test_feature_flag_rollback.py tests/test_health.py tests/test_m3_issue_focus.py` | 29 passed |
| `cd apps/web; npm run test` | 50 passed |
| `cd apps/web; npm run build` | passed |

## 浏览器验收

| 项目 | 结果 |
| --- | --- |
| 本地 Web 加载 | passed |
| 本地 API health | passed |
| 基础结果列表渲染 | passed |
| 详情抽屉打开 | passed |
| 争议焦点区块有来源入口 | passed |
| 关键要素区块有来源入口 | passed |
| 来源入口可点击定位 | passed |
| 详情页白屏 | no |
| 禁止的结果倾向或绝对覆盖话术可见 | no |

说明：浏览器详情 UI 使用前端测试数据验收；后端当前代码通过 TestClient 和 pytest 覆盖。

## 结论

M3-3 结论：GO。

下一步新会话标题：类案检索助手 M3-4 相似事实对比。
