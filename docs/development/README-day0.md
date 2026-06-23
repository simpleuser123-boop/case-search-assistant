# 开发环境启动（Day 0 · 4.1 + 4.2）

本目录是「类案检索助手」MVP 的工程骨架。Day 0 仅完成 4.1（配置/密钥）与 4.2（基础服务），数据/索引/评测（4.3–4.5）待后续。

## 1. 配置密钥（4.1）

```bash
cp .env.example .env
# 推荐把 DEEPSEEK_API_KEY 配到系统环境变量；.env 仅作本地兜底
```

DeepSeek 官方 base URL 使用 `https://api.deepseek.com`。`.env` 不入库，启动时只校验密钥是否存在，绝不打印值。

## 2. 起基础服务（PostgreSQL + Redis）

```bash
docker compose -f infra/docker-compose.yml up -d
```

## 3. 后端 apps/api

```bash
cd apps/api
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000           # 访问 http://localhost:8000/health
pytest                                               # 跑示例测试
```

## 4. 前端 apps/web

```bash
cd apps/web
npm install
npm run dev      # http://localhost:5173
npm test         # Vitest 示例测试
```

## 5. 验收清单（4.2）

- [ ] `/health` 返回 `status=ok`，`secrets_present` 仅布尔、不含值
- [ ] PostgreSQL 可连接（`db_reachable=true`）
- [ ] Chroma 持久化目录可读写（`chroma_dir_writable=true`）
- [ ] `pytest` 与 `npm test` 均可跑通
- [ ] 前端首页可打开

## 6. 数据管道（4.3 解析 + 切分）

数据源：JuDGE 基准 `all.json`（2505 份刑事判决全文）。脚本 `apps/api/app/pipeline/parse_judge.py` 把全文解析为 `CaseDocument` 元数据与 `CaseChunk` 切片。

```bash
python apps/api/app/pipeline/parse_judge.py \
  --input <JuDGE>/data/all.json \
  --out data/processed
# 可选：--limit N 只跑前 N 条，--sample K 打印 K 条抽样
```

产出（已生成，落在 `data/processed/`）：

- `cases.jsonl` — 2505 份案例元数据（case_no/court/court_level/trial_level/case_cause/judgment_date/region/crime_type/law_articles…）
- `chunks.jsonl` — 9621 个切片（chunk_type ∈ fact/court_found/court_opinion/judgment_result，带 offset）
- `quality_report.json` — 质量门禁报告

质量门禁实测（全部达标）：案号缺失 1.28%（<2%）、法院缺失 0%、日期缺失 0%、空 chunk 0%、重复 0%、本院查明识别率 63.67%（≥60%）。

> 注意：JuDGE 仅刑事案件；其自带 `qrels_file_test` 是「案件→法条」标注，非「案件→相似案件」，不能直接用作类案检索评测集（4.5 评测集需另建）。

## 7. 向量索引（4.4 embedding → Chroma）

脚本 `apps/api/app/pipeline/index_chroma.py` 把 `chunks.jsonl` 用本地 bge-m3（Ollama）向量化后写入 Chroma collection `case_chunks_bge_m3_v1`（cosine 距离）。**此步在本机调用本地 Ollama 服务，约 9600 次 embedding，数据不出本机、无需 API 密钥。**

本机 Windows 环境下，Chroma 持久化目录使用 `.env` 中的 ASCII 路径 `C:/Users/yyl/Desktop/case_search_chroma_bge_m3`。不要写回中文项目路径下的 `./data/chroma`，该路径曾出现 HNSW 文件缺失导致 collection 不能查询。

```bash
# 0) 准备：本机已 `ollama pull bge-m3` 且 Ollama 服务在运行（默认 http://localhost:11434）；装依赖
pip install chromadb

# 1) 冒烟（先跑 50 条，确认服务通、维度正确、能检索）
python apps/api/app/pipeline/index_chroma.py \
  --chunks data/processed/chunks.jsonl --cases data/processed/cases.jsonl \
  --limit 50 --smoke

# 2) 全量（9621 个 chunk）
python apps/api/app/pipeline/index_chroma.py \
  --chunks data/processed/chunks.jsonl --cases data/processed/cases.jsonl

# 3) 复验现有索引（不重建，跑自身召回 + 5 条手工 query）
python apps/api/app/pipeline/index_chroma.py \
  --chunks data/processed/chunks.jsonl --cases data/processed/cases.jsonl \
  --resume --smoke --query-smoke
```

Ollama 不可用环境可用 `--dry-run` 验证写入/检索逻辑（用确定性伪向量，不调服务、不可用于真实检索）。

向量维度由首次响应固化并写入报告；query 与文书 embedding 必须共用同一 provider/模型/维度（见 `config.py` 红线）。每个向量绑定 9 个过滤元数据字段：case_id/chunk_id/chunk_type/case_cause/court_level/trial_level/judgment_year/region/text_hash。

状态（2026-06-05）：本机真实 bge-m3 + Chroma 索引已验证通过。collection `case_chunks_bge_m3_v1` 位于 `C:/Users/yyl/Desktop/case_search_chroma_bge_m3`，count=9621，元数据为 `embedding_provider=ollama`、`model_name=bge-m3`、`vector_dimension=1024`、`distance_metric=cosine`。`--resume --smoke --query-smoke` 实测 `embedding_success_rate=100.0`，近邻自检 `self_recall=true`，5 条手工 query 均返回候选。

## 8. 评测集与兜底映射（4.5）

评测数据使用本机 LeCaRDv2 GitHub 目录：`C:/Users/yyl/Downloads/LeCaRDv2-main`。[LeCaRDv2 官方 README](https://github.com/THUIR/LeCaRDv2) 说明该数据集包含 800 条 query、55,192 份 candidate case，并提供 TREC qrels 相关性标注；本机已在 `candidate/` 下补齐 `candidate.tar` 正文包，脚本可直接读取 tar，无需先解压。

```bash
# 1) 标准化 LeCaRDv2 test split 的 query/qrels，并生成术语映射
python apps/api/app/eval/prepare_lecardv2_eval.py \
  --lecard-root C:/Users/yyl/Downloads/LeCaRDv2-main \
  --out data/eval --split test

# 2) BM25 baseline。候选正文缺失时会输出 blocked_missing_candidate_corpus；
#    当前本机 candidate.tar 已补齐，可直接生成正式指标
python apps/api/app/eval/bm25_baseline.py \
  --queries data/eval/lecardv2_queries.jsonl \
  --qrels data/eval/lecardv2_qrels.jsonl \
  --corpus C:/Users/yyl/Downloads/LeCaRDv2-main/candidate \
  --out data/eval/bm25_baseline_report.json
```

当前产出：

- `data/eval/lecardv2_queries.jsonl` — 160 条 test query，使用 `fact` 作为检索 query 文本。
- `data/eval/lecardv2_qrels.jsonl` — 4,795 条相关性标注，label 0/1/2/3，`label >= 2` 视为相关。
- `data/eval/term_mappings.json` — 20 组法律术语/兜底映射。
- `data/eval/lecardv2_eval_report.json` — 4.5 门禁报告。
- `data/eval/bm25_baseline_report.json` — 全量 test split BM25 baseline 指标与每条 query 的 top10 结果。

状态（2026-06-05）：4.5 已达标并完成 baseline：

- 标准化评测集：`query_count=160`、`qrels_count=4795`、`queries_with_relevant_labels=159`、`term_mapping_count=20`。
- candidate 正文：`candidate.tar` 已识别，`corpus_doc_count=55192`、`indexed_doc_count=55192`。
- BM25 baseline：`status=ok`、`query_count=160`、`Precision@5=0.4987`、`NDCG@10=0.4442`。

该 BM25 是 Day0 可复现基线（法律术语 + 中文 bigram 的标准库实现），不是 LeCaRDv2 论文/官方 pyserini BM25 的严格复现；后续 Day1/Day2 的向量召回和混合召回可统一对照此报告。
