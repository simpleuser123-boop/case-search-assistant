# M3-6 案例对比视图受控入口 · Go/No-Go 报告

- 步骤：M3-6 案例对比视图受控入口
- 时间：2026-06-13 05:45:41
- 结论：**GO**
- 范围：仅前端（apps/web）。后端 Python 改动文件数 = 0。

## 1. 入场依据（已确认仍成立）

| 前置门禁 | 状态 |
| --- | --- |
| M2-8 基础搜索 | GO |
| M2 可信检索 | GO |
| M3 入口 | GO |
| M3-1 阅读入口合同 | GO |
| M3-2 裁判要旨摘要闭合 | GO |
| M3-3 争议焦点与关键要素 | GO |
| M3-4 相似事实对比 | GO |
| M3-5 相似片段高亮 | GO |
| ENABLE_WEIGHTED_RERANK | 默认 false，未改动 |

来源/召回/rerank 默认、扩展检索默认、在线排序均未改动。

## 2. 实现方式

`case_compare` 采用**纯前端组装**：从本次会话已获取的数据（案例详情 + 懒加载事实对比 + 当前结果响应中的风险提示）在内存中聚合，不新增后端持久化、不新增 API。选择集合只存活在 React state，每次新检索清空，离开会话即丢失——结构上即满足「不写入长期存储 / 不跨会话恢复 / 不参与排序」。

数据结构字段：`selected_case_ids`、`compare_sections`、`source_anchors`、`module_status`、`degrade_reason`。

对比维度（5 个）：元数据、裁判要旨摘要、争议焦点与关键要素、事实维度、风险提示与不利线索。

## 3. 受控入口与边界

| 规则 | 落地 |
| --- | --- |
| 选择来源 | 仅当前可见结果（主结果 + 低置信候选 + 扩展候选） |
| 数量限制 | 最少 2、最多 3 |
| 持久化 | 无；ephemeral React state |
| 跨会话恢复 | 无 |
| 收藏/历史/清单/导出/报告 | 未实现 |
| 选择脱离当前结果池 | 自动剔除 |
| 反向影响排序 | 无 |

## 4. 来源规则

每个案例侧对比单元必须有指向**本案**且可定位 chunk 的来源锚点，否则降级并给出 reason code，无来源内容不展示。跨案锚点被过滤，绝不借用其他案例的来源。元数据为案卷目录数据，锚定 `case_record`。降级 reason code：`detail_unavailable / detail_loading / module_degraded / missing_source_anchor / source_chunk_unavailable / no_anchored_content / no_flagged_risk`。

## 5. 前端视觉规则

对比视图为独立 overlay（role=dialog），与主结果列表分离；可通过「关闭对比」按钮或 Esc 关闭，关闭后主结果与排序不受影响；移动端降级为下拉选择单案例、分维度堆叠查看。

## 6. 验证结果

| 命令 | 结果 |
| --- | --- |
| `npx tsc --noEmit`（web） | pass（exit 0） |
| `vitest run src/lib/caseCompare.test.ts` | 11 passed |
| `vitest run src/pages/CaseCompareAcceptance.test.tsx` | 5 passed |
| `npm run test`（全量） | 9 files / 79 tests passed，0 回归 |
| `vite build`（干净目录） | pass，105 modules |
| API smoke（pytest 三项） | **未运行**，见下 |

### API smoke 未运行说明

`pytest tests/test_search_api_fallback_smoke.py tests/test_feature_flag_rollback.py tests/test_performance_smoke.py` 在本环境无法执行：仓库 `.venv` 为 Windows 侧创建，Linux VM 内不可用；VM 网络在时限内无法安装 fastapi/chromadb 等依赖。

不掩盖该缺口，同时给出可信替代证据：**M3-6 未改动任何 Python 文件**（后端改动 = 0），这三项 smoke 覆盖的是搜索 fallback / feature flag rollback / 性能路径，与本步骤无关，且其字节与 M2 最终验证（2026-06-12，全部通过）一致。

### 浏览器验收（jsdom 真实组件树）

原生浏览器验收因 host↔VM 网络桥不可达而无法进行；改用项目既有 jsdom 谐振（驱动真实组件树、走内置 mock 数据路径）覆盖可见验收点：

| 验收点 | 结果 |
| --- | --- |
| 选择案例 | pass |
| 打开对比 | pass |
| 五个维度均呈现 | pass |
| 每个案例侧单元有来源锚点或安全降级 | pass |
| 关闭对比 | pass |
| 关闭后主结果仍可用 | pass |
| 无导出/收藏/历史/报告/清单入口 | pass |
| console error count | 0 |

## 7. 正文泄露检查

本报告与 JSON 仅含字段名、count、status、reason code、feature flag 状态、指标摘要与测试结果；不含原始 query、案情正文、候选正文、chunk 正文、裁判文书正文或用户自由文本。对比的可观测日志 `case_compare_render` 同样只含 count/status/reason code。

## 8. 验收（全部满足）

- 对比内容有来源锚点或安全降级：**满足**
- 对比视图可关闭、可回滚：**满足**
- 对比视图不改变主结果排序：**满足**
- 未实现 M4 工作流能力（收藏/历史/导出/清单/报告/团队复用）：**满足**
- 无正文泄露：**满足**

## 9. 止损（均未触发）

- 对比视图持久化用户选择：否
- 引入收藏/历史/导出/报告：否
- 导致主链路不可用：否

## 10. 结论

**GO**。M3-6 在受控边界内完成案例横向对比视图：前端纯组装、不落库、不跨会话、不参与排序，案例侧单元来源锚定或安全降级，无正文泄露，未引入任何 M4 工作流能力。前端全量测试（79）与构建通过，新增 16 项 focused/acceptance 测试全绿。唯一未执行项为后端 pytest smoke（环境依赖不可装），已用「后端零改动 + 与 M2 终验一致」说明，不构成阻断。可进入 M3-7。

### 遗留与建议

- 在可联网或已装依赖的环境补跑一次三项 API smoke 作为形式确认（预期不变，因后端未改）。
- 写入磁盘存在偶发截断（Windows 挂载刷盘问题），本轮已对 caseCompare.ts / ResultCard.tsx / ResultList.tsx / SearchPage.tsx 逐个用 esbuild parse + tsc 校验并修复，最终状态完整。
