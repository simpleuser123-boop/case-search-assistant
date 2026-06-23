# M5-8 法院/法官倾向分析（F19）展示与边界 — 验收报告

- 时间：2026-06-15 02:40:38
- 步骤：M5-8（M5 商业化扩展第 8 步）
- 结论：**GO**
- 前置门禁：M5-7 数据门禁 **PASS**（`m5-tendency-data-gate-20260614-134042`，gate_version `m5-7-tendency-data-gate-v2`，`f19_can_go_live=true`）

## 1. 目标与边界

在 **M5-7 数据门禁达标的前提下**，输出法院/法官倾向分析。展示以统计透明为核心：标注样本量与覆盖范围、可追溯到来源、明确表达不确定性；**不预测个案诉讼结果、不输出胜负概率、不输出确定性法律结论、不展示个案正文、不针对具名法官输出预测**。法官维度按 M5-7 路线B方案B已移除，F19 收窄为法院层级 / 审级 / 案件领域 / 案由的统计透明。

两道闸（缺一即不展示，回到 M5-7 末态）：

1. `ENABLE_TENDENCY_ANALYSIS=true`（后端 config 默认 false；前端 `VITE_ENABLE_TENDENCY_ANALYSIS` 默认 false）；
2. M5-7 数据门禁 `f19_can_go_live=True`。

任一不满足 → 后端 `403 TENDENCY_ANALYSIS_UNAVAILABLE`，前端展示「暂不可用」且不渲染任何聚合。

## 2. 后端实现

新增只读包 `apps/api/app/tendency_analysis/`：

- `models.py`：`TendencyBucket` / `TendencyAggregation` / `TendencyAnalysisResult` 纯结构。每个 bucket 带 `sample_size` / `share` / `sample_sufficient` / `case_id_refs`（引用，截断 ≤20）/ `case_id_total`。
- `aggregate.py`：只读聚合，复用 M5-7 `metrics.py` 的迭代器与领域分类器，对 4 维度计数。单分组样本门槛 `MIN_SAMPLE_PER_BUCKET=30`；只收集 `case_id` 引用，绝不收集任何正文/当事人字段。
- `service.py`：门禁 + flag 联动；强制注入样本量 / 覆盖范围 / 数据来源 / 不确定性说明 + 免责；`case_cause` 维度仅展示达标分组并截断到前 20；落盘/返回前 `assert_analysis_output_clean` fail-closed。
- `privacy.py`：在 M5-7 门禁护栏基础上补当事人/全文键与倾向分析误用话术；具名法官预测正则同口径。
- API：`apps/api/app/api/tendency.py` → `GET /api/tendency/analysis`，注册进 `main.py`。日志只记路径/原因码/计数，不落正文/凭据。

数据源：`data/processed/tendency_corpus_meta.jsonl`（裁判文书网备份只读元数据，零正文/零当事人；存在则用，否则回落 `cases.jsonl`）。

## 3. 前端实现

- `featureFlags.ts`：新增 `isTendencyAnalysisEnabled()`，默认 false。
- `services/tendencyApi.ts`：只读 GET 客户端；403 归类为 `unavailable`。
- `components/tendency/TendencyAnalysisPanel.tsx`：flag-gated，关闭即渲染 `null`。展示样本量 + 覆盖范围 + 数据来源 + 各维度分布（达标分组解读占比，样本不足分组标注「样本不足，不解读占比」）+ 可追溯 case_id 引用 + 常驻强制免责。

## 4. 免责说明（强制常驻）

> 本分析为基于现有数据覆盖的聚合统计参考，可能未覆盖全部案例，存在抽样与时间范围偏差；不构成法律意见，不预测个案结果，不代表任何具名法官/法院的裁判倾向，样本不足的维度不作解读，所有结论需结合个案与人工复核独立判断。

## 5. 验证

| 项 | 结果 |
|---|---|
| 后端 focused `test_m5_tendency_analysis.py` | 15 passed |
| 后端必跑（health + fallback_smoke + feature_flag_rollback + performance_smoke） | 全通过 |
| 后端合并回归（+gate/bulk/sharing/permission/team_reuse/release_gate） | 133 passed, rc=0 |
| 前端 `TendencyAnalysisPanel.test.tsx` | 4 passed |
| 前端全量（分批 components/lib/services/pages） | 270 passed（266 既有 + 4 新增） |
| `vite build` | 118 modules transformed, clean outDir rc=0 |
| 新增源码禁用话术扫描 | 命中=0（仅护栏定义/否定式表达） |

本地验证观测（flag 开启时）：总样本 80996；法院层级 基层 68018 / 中级 11898 / 高级 991；审级 一审 62961 / 执行 8978 / 二审 7907；案件领域 civil 59154 / criminal 15340 / execution 5146；案由展示 20 个达标分组。**注：仅为本地观测，`ENABLE_TENDENCY_ANALYSIS` 仍默认 false，生产不展示。**

## 6. 止损（NO_GO）项核查 — 全部未触发

- 门禁未达标仍展示：否（双闸，门禁 fail → 403）。
- 输出个案胜负预测 / 概率 / 确定性结论：否（隐私护栏 fail-closed + 测试守门）。
- 展示个案正文：否（只聚合计数 + case_id 引用）。
- `ENABLE_TENDENCY_ANALYSIS` 默认开启：否（前后端默认 false）。
- 倾向分析改变既有默认行为 / 主排序：否（只读，不 import rerank/retrieval）。

## 7. 边界守恒

数据 / 向量库 / 评测集 / 主排序 / `config.py` flag 默认值均未改动；倾向分析不参与召回 / source selection / rerank。

## 8. 下一步

M5-9 商业化闭环（前置：M5-8 GO 已满足）。
