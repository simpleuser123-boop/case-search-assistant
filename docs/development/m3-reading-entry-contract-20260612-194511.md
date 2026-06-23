# M3-1 阅读提效入口合同

生成时间：2026-06-12 19:45:11 +08:00

## 范围

本步骤只冻结 M3 阅读提效入口合同，不实现 M3-2 及之后的裁判要旨摘要、争议焦点、事实对比、高亮、案例对比或引用复制能力。

本报告和配套 JSON 只记录字段名、count、状态、reason code、feature flag 状态、指标摘要、测试结果和结论，不保存原始 query、案情正文、候选正文、chunk 正文、裁判文书正文或用户输入自由文本。

## 必读输入

| 输入 | 读取结论 |
| --- | --- |
| `落地设计文档/13-M3阅读提效分步骤文档.md` | M3 定位为阅读提效；M3-1 只冻结入口合同；M3-2 至 M3-7 必须后续分步执行 |
| `落地设计文档/01-演进计划总览.md` | M3 从“找到”升级到“快速判断能不能用”；M4 的历史、收藏、导出和报告不属于 M3 |
| `落地设计文档/03-前端架构与设计系统.md` | 前端需要明确区分 AI 摘要、来源引用、降级状态和隐私提示 |
| `落地设计文档/04-数据层设计.md` | 用户原始案情不应持久化；来源、索引、日志和评测边界必须可追溯 |
| `落地设计文档/06-完整功能拆解.md` | 阅读辅助应建立在检索可信和来源可核验之上 |
| `落地设计文档/07-开发步骤.md` | Sprint 4 才进入阅读提效；回滚、性能、日志脱敏和来源锚点仍是硬门禁 |
| `落地设计文档/12-M2可信检索分步骤文档.md` | M2 成功标准要求来源锚点、隐私、低置信度分离、扩展检索回滚和默认开关关闭 |
| M2-8 产物 | 基础搜索 GO、M2 可信检索 GO、M3 入口 GO |
| 最新 M2 合同与闭环产物 | M2-1 至 M2-7 全部 GO，可作为 M3-1 入场依据 |
| `apps/api/app/core/config.py` / `.env.example` | `ENABLE_WEIGHTED_RERANK=false` 当前成立 |

## M3 Entry Register

### M2-8 三类结论

| 结论项 | 状态 | 证据 |
| --- | --- | --- |
| 基础搜索继续可用 | GO | `docs/development/m2-final-verification-20260612-191945.md`；`docs/development/m2-release-gate-20260612-191945.json` |
| M2 可信检索完成 | GO | `docs/development/m2-final-verification-20260612-191945.md`；`docs/development/m2-parameter-version-20260612-191945.json` |
| M3 入口 | GO | M2-8 final verification 和 release gate 均记录 `m3_entry=GO` |

### M2-1 至 M2-7 最新产物

| 步骤 | 最新产物 | 状态 | M3 继承口径 |
| --- | --- | --- | --- |
| M2-1 | `docs/development/m2-entry-contract-20260612-100850.md` / `.json` | GO | 字段合同、禁止字段、默认 flag 关闭、离线评测隔离 |
| M2-2 | `docs/development/m2-source-anchor-closure-20260612-104120.md` / `.json` | GO | 用户可见 AI 加工内容必须有 `source_anchors`；无锚点内容隐藏或降级 |
| M2-3 | `docs/development/m2-coverage-privacy-20260612-140115.md` / `.json` | GO | coverage 不编造；用户输入不持久化；前端不持久化原始 query |
| M2-4 | `docs/development/m2-low-confidence-candidates-20260612-151624.md` / `.json` | GO | 主结果与低置信度候选分离；qrels、label、relevance 不进运行时分层 |
| M2-5 | `docs/development/m2-expanded-search-gate-20260612-155403.md` / `.json` | GO | 扩展检索受控入口；标准搜索默认行为不变；可回滚 |
| M2-6 | `docs/development/m2-feedback-loop-20260612-171213.md` / `.json` | GO | 反馈事件只保存脱敏字段；不影响排序、rerank 或 source selection |
| M2-7 | `docs/development/m2-risk-hints-20260612-181106.md` / `.json` | GO | 风险提示必须有来源锚点；只表达复核线索；不进入排序 |

### M2 继承门禁

| 门禁 | M3-1 冻结口径 |
| --- | --- |
| 来源锚点 | 案例侧 AI 加工内容展示前必须有 `source_anchors`，至少包含 `case_id` 和 `source_chunk_id` |
| 隐私 | 用户原始案情、自由文本和长上下文只允许请求内临时使用，不进入报告、JSON、日志或测试快照 |
| 文案 | 只表达阅读定位、复核线索、对照维度；不表达确定性法律结论、诉讼结果概率或绝对覆盖承诺 |
| 性能 | M3 能力可懒加载、超时降级，不造成主搜索或详情页基础信息白屏 |
| 回滚 | M3 能力必须可通过 feature flag 关闭，或在无锚点、模型失败、超时、片段不足时安全降级 |
| 排序隔离 | 不改变线上排序、source selection、rerank 默认开关或 M2 扩展检索默认行为 |
| 评测隔离 | qrels、label、relevance 只允许离线评测，不得进入运行时摘要、分层、对比、高亮、排序或分组 |
| ID 特判隔离 | 不得根据 query id、case id 做排序、摘要、对比或高亮特判 |

### 当前 Feature Flag 证据

| Flag | 当前证据 | M3-1 结论 |
| --- | --- | --- |
| `ENABLE_WEIGHTED_RERANK` | `apps/api/app/core/config.py=false`；`.env.example=false`；M2-8 release gate 为 GO | 必须继续保持 false |
| `ENABLE_SUMMARY` | `apps/api/app/core/config.py=false`；`.env.example=false` | M3-2 不得借入口合同默认打开摘要能力 |
| `ENABLE_EXPANDED_SEARCH` | `apps/api/app/core/config.py=false`；`.env.example=false` | M3 flag 不得改变 M2 扩展检索默认关闭态 |
| `VITE_ENABLE_EXPANDED_SEARCH` | `.env.example=false` | 前端镜像不得绕过后端 flag |

## M3 阅读辅助合同

| 能力字段 | API 合同 | 前端展示合同 | 日志/报告/JSON 合同 | 降级合同 | 排序边界 |
| --- | --- | --- | --- | --- | --- |
| `holding_summary` | 可包含 `summary_items`、`source_anchors`、`confidence`、`generation_status`、`degrade_reason`；每个案例侧摘要项必须有锚点 | 只在摘要项有可点击来源入口时展示；无锚点时不展示 AI 摘要 | 只记录 `summary_item_count`、`anchor_count`、`status`、`reason_code`、`latency_ms`；不记录摘要正文 | 无锚点、模型失败、片段不足或来源不一致时返回空状态或来源片段入口 | 不得影响搜索排序、候选召回、source selection |
| `issue_focus` | 可包含 `items[].label`、`category`、`source_anchors`、`confidence`、`degrade_reason`；展示项必须有锚点 | 作为详情页阅读导航，不覆盖 M2 风险提示，不输出结论性文案 | 只记录 `item_count`、`category_count`、`status`、`reason_code`；不记录焦点正文 | 无锚点、类别越界或生成失败时隐藏对应项 | 不得作为排序特征，不得使用离线评测字段或 ID 特判 |
| `key_elements` | 可包含 `items[].label`、`category`、`source_anchors`、`confidence`、`degrade_reason`；只表达法院认定或裁判理由中的阅读要素 | 作为可复核要素列表或导航，不表达胜败倾向 | 只记录 `element_count`、`category_count`、`anchor_count`、`reason_code`；不记录要素正文 | 无来源要素不展示；来源不稳定时为空状态 | 不得进入 rerank、分组或高亮特判 |
| `fact_alignment` | 可包含 `dimension`、`query_side_signal`、`case_side_facts`、`source_anchors`、`match_type`、`confidence`、`degrade_reason`；用户侧信号仅请求内临时抽象 | 展示“相同维度”“相近维度”“需复核差异”等阅读线索；案例侧事实必须有来源入口 | 只记录 `dimension_count`、`match_type_count`、`status`、`reason_code`；不记录用户输入或事实正文 | 用户侧抽象失败、案例侧无锚点、超时或模型失败时降级为空状态 | 不得改变主排序，不得触发扩展检索默认行为，不得用 query id/case id 特判 |
| `similarity_highlights` | 可包含 `highlight_id`、`case_id`、`source_chunk_id`、`anchor_type`、`related_module`、`display_status`、`degrade_reason` | 只用于定位来源片段；片段不可用时显示降级状态 | 只记录 `highlight_count`、`related_module`、`status`、`reason_code`；不记录 chunk 正文 | 锚点缺失、片段不可用或定位失败时隐藏高亮或展示来源片段入口 | 不得影响搜索排序、详情排序或相关性判断 |
| `case_compare` | 可包含 `selected_case_ids`、`compare_sections`、`source_anchors`、`module_status`、`degrade_reason`；只允许当前结果内少量案例 | 对比视图与主结果分离，可关闭；不保存为收藏、历史、清单或报告 | 只记录 `selected_count`、`section_count`、`status`、`reason_code`；不记录对比正文 | 无来源单元隐藏；对比失败不影响主结果和详情基础信息 | 对比选择不得反向调整排序，不得把低置信度候选包装为主结果 |
| `citation_copy` | 可包含 `case_id`、`case_number`、`court`、`trial_level`、`judgment_date`、`citation_format`、`copy_status`；仅限元数据 | 只复制单案或用户明确选择的少量案例基础引用格式；无导出、历史、收藏、清单或报告入口 | 只记录 `event_type`、`count`、`status`、`reason_code`；不记录复制正文或用户输入 | 剪贴板不可用时提示失败，不影响主搜索、详情或对比 | 复制行为不得影响排序、推荐、对比选择或任何检索权重 |

## Source Anchors 规则

`source_anchors` 是 M3 案例侧 AI 加工内容的展示前置条件。最小结构：

```text
source_anchors[]:
  case_id: string
  source_chunk_id: string
  chunk_type: optional string
  anchor_type: optional enum
  source_ref: optional string
```

硬规则：

- 案例侧 AI 加工内容必须有 `source_anchors`。
- `source_anchors` 至少包含 `case_id` 和 `source_chunk_id`。
- 无 `source_anchors` 的案例侧 AI 内容不得展示，只能降级为空状态或来源片段入口。
- 不允许创建伪锚点、模糊锚点或为通过测试手工绑定假锚点。
- 用户侧输入只允许在请求内做临时抽象引用，不持久化原文，不写入日志、报告、JSON 或测试快照。

## M3 Feature Flag 策略

M3-1 不新增运行时开关，不改变现有配置默认值。后续 M3 步骤如引入 flag，必须满足：

| 策略项 | 合同 |
| --- | --- |
| Weighted rerank | `ENABLE_WEIGHTED_RERANK=false` 必须继续成立 |
| M3 总开关 | 可引入 `ENABLE_M3_READING_ASSIST`，默认关闭；后端为权威 |
| M3 前端镜像 | 可引入 `VITE_ENABLE_M3_READING_ASSIST`，只能隐藏或显示入口，不能绕过后端 |
| 模块级开关 | 每个阅读辅助模块必须能关闭或降级；关闭后不影响标准搜索 |
| 标准搜索隔离 | 任何 M3 flag 都不得改变 `/api/search` 标准入口的默认排序、source selection、rerank、扩展检索默认行为 |
| 安全降级 | flag 关闭、模型失败、锚点缺失、超时或来源不足时，返回空状态、隐藏模块或展示来源片段入口 |

## 禁止字段与正文边界

开发报告、JSON、日志、埋点和测试快照只允许保存：

- 字段名。
- count。
- 状态。
- reason code。
- feature flag 状态。
- 指标摘要。
- 测试结果。
- GO/NO_GO 结论。

禁止保存或快照化：

- 原始 query。
- 案情正文。
- 候选正文。
- chunk 正文。
- 裁判文书正文。
- 用户输入自由文本。
- 可反推出原文的长片段上下文。
- 未锚定的 AI 生成解释。

## 验证

| 命令 | 结果 |
| --- | --- |
| `cd apps/api; pytest tests/test_m1_3_candidate_comparison.py tests/test_m1_3_legal_candidate_robustness.py tests/test_feature_flag_rollback.py tests/test_health.py tests/test_search_api_fallback_smoke.py tests/test_summary_service.py tests/test_performance_smoke.py` | 60 passed |

本步骤未新增 M3 focused contract tests，因此没有新增测试命令需要纳入。

## Go / No-Go

结论：GO。

理由：

- M2-8 入场证据完整，基础搜索、M2 可信检索、M3 入口均为 GO。
- M2-1 至 M2-7 最新产物全部存在且结论为 GO。
- `ENABLE_WEIGHTED_RERANK=false` 在 `apps/api/app/core/config.py` 和 `.env.example` 中继续成立。
- M3 合同已冻结字段、来源锚点、禁止字段、隐私、日志、feature flag 和降级策略。
- M3 合同明确不改变基础搜索默认行为，不使用 qrels、label、relevance 或 query id/case id 做运行时特判。
- 本报告和 JSON 产物未保存正文型内容。

允许进入下一步：

```text
类案检索助手 M3-2 裁判要旨摘要来源闭环
```
