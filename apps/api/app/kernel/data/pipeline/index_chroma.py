# -*- coding: utf-8 -*-
"""4.4 向量索引：chunks.jsonl -> 本地 bge-m3（Ollama）-> Chroma collection。

设计依据：落地设计文档/04-数据层设计.md §4。
- collection: case_chunks_bge_m3_v1（绑定单一模型版本，cosine 距离）
- 元数据过滤字段：case_id / chunk_id / chunk_type / case_cause / court_level /
  trial_level / judgment_year / region / text_hash
- query 与文书 embedding 必须共用同一 provider/模型/维度（config 红线）。
- 维度由首次响应校验并固化（§4.1）；bge-m3 为 1024 维。
- 本地推理：经 Ollama /api/embed 调用，无需 API key、数据不出本机。

前置：本机已 `ollama pull bge-m3` 且 Ollama 服务在运行（默认 http://localhost:11434）。
运行（本机）：
  python index_chroma.py --chunks data/processed/chunks.jsonl \
      --cases data/processed/cases.jsonl --limit 50                           # 冒烟，默认读 CHROMA_PERSIST_DIR
  python index_chroma.py --chunks ... --cases ...                            # 全量
沙箱验证（无服务、无网络）：
  python index_chroma.py --chunks ... --cases ... --dry-run --limit 50
"""
from __future__ import annotations
import os, json, time, argparse, hashlib
from pathlib import Path

COLLECTION = "case_chunks_bge_m3_v1"
EXPECTED_OLLAMA_MODEL = "bge-m3"
BATCH = 8  # 纯 CPU + 有限内存下的稳妥批量；过大易拖慢或占满内存，可按机器调整。

QUERY_SMOKE_CASES = [
    "危险废物废活性炭未经审批倾倒污染环境罪",
    "酒后驾驶撞人死亡后逃逸交通肇事罪",
    "非法占用林地退耕还林地种植农作物",
    "高额利息向不特定对象吸收资金非法吸收公众存款",
    "容留他人吸毒并贩卖甲基苯丙胺毒品",
]


class EmbeddingError(RuntimeError):
    def __init__(self, status_code, message=""):
        self.status_code = status_code
        self.message = message
        super().__init__(f"Ollama embedding 失败: status={status_code}, message={message}")


def load_cases_meta(cases_path: str) -> dict:
    """case_id -> 过滤用元数据（仅取 Chroma 需要的字段）。"""
    meta = {}
    with open(cases_path, encoding="utf-8") as f:
        for line in f:
            d = json.loads(line)
            year = 0
            if d.get("judgment_date"):
                try:
                    year = int(d["judgment_date"][:4])
                except ValueError:
                    year = 0
            meta[d["case_id"]] = {
                "case_cause": d.get("case_cause", "") or "",
                "court_level": d.get("court_level", "") or "",
                "trial_level": d.get("trial_level", "") or "",
                "judgment_year": year,
                "region": d.get("region", "") or "",
                "text_hash": d.get("text_hash", "") or "",
            }
    return meta


def iter_chunks(chunks_path: str, limit: int = 0):
    with open(chunks_path, encoding="utf-8") as f:
        for i, line in enumerate(f):
            if limit and i >= limit:
                break
            yield json.loads(line)


# ---------- embedding 后端 ----------
def embed_dryrun(texts, dim=1024):
    """确定性伪向量：用文本 hash 生成稳定向量，便于在无密钥/无网络环境验证写入与检索逻辑。"""
    import struct
    out = []
    for t in texts:
        h = hashlib.sha256(t.encode("utf-8")).digest()
        # 用 hash 反复填充到 dim 维，再做 L2 归一化
        raw = (h * ((dim * 4) // len(h) + 1))[: dim * 4]
        vec = [struct.unpack_from(">i", raw, i * 4)[0] / 2**31 for i in range(dim)]
        norm = sum(x * x for x in vec) ** 0.5 or 1.0
        out.append([x / norm for x in vec])
    return out


def embed_ollama(texts, model: str, base_url: str):
    """真实 bge-m3 embedding（本地 Ollama /api/embed）。失败抛异常，由调用方决定重试。
    仅用标准库 urllib，不引入额外依赖。"""
    import urllib.request
    import urllib.error
    url = base_url.rstrip("/") + "/api/embed"
    payload = json.dumps({"model": model, "input": texts}).encode("utf-8")
    req = urllib.request.Request(url, data=payload,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=600) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise EmbeddingError(exc.code, exc.reason or "")
    except urllib.error.URLError as exc:
        # 服务未启动 / 连接被拒等：用 503 触发上层短重试逻辑
        raise EmbeddingError(503, str(getattr(exc, "reason", exc)))
    embs = body.get("embeddings")
    if not embs or len(embs) != len(texts):
        raise EmbeddingError(500, f"返回 embeddings 数量异常: got {len(embs) if embs else 0}, want {len(texts)}")
    return embs


def embed_ollama_resilient(texts, model: str, base_url: str):
    """真实 embedding：批量失败时拆分，临时服务异常时短重试。"""
    for i in range(3):
        try:
            return embed_ollama(texts, model, base_url)
        except EmbeddingError as exc:
            if exc.status_code == 400 and len(texts) > 1:
                mid = len(texts) // 2
                return (
                    embed_ollama_resilient(texts[:mid], model, base_url)
                    + embed_ollama_resilient(texts[mid:], model, base_url)
                )
            if exc.status_code in {429, 500, 502, 503, 504} and i < 2:
                time.sleep(2 ** i)
                continue
            raise


def validate_collection_model(model: str, *, dry_run: bool = False):
    if dry_run:
        return
    if model != EXPECTED_OLLAMA_MODEL:
        raise SystemExit(
            f"{COLLECTION} 只能写入/查询 {EXPECTED_OLLAMA_MODEL} 向量；"
            f"当前模型是 {model}。不同 embedding 模型必须使用独立 collection。"
        )


# ---------- 主流程 ----------
def _load_dotenv_fallback(start: Path):
    """从项目根向上查找 .env，把其中变量补进 os.environ（系统环境变量优先，不覆盖已有）。
    仅用标准库，不引入 python-dotenv 依赖。已存在于环境变量中的项不会被改写。"""
    cur = start.resolve()
    for d in [cur, *cur.parents]:
        env = d / ".env"
        if env.is_file():
            for raw in env.read_text(encoding="utf-8").splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                k, v = k.strip(), v.strip().strip('"').strip("'")
                if k and k not in os.environ:  # 系统环境变量优先
                    os.environ[k] = v
            return str(env)
    return None


def run(chunks_path, cases_path, persist_dir=None, limit=0, dry_run=False,
        model=None, batch=BATCH, resume=False, base_url=None):
    _load_dotenv_fallback(Path(chunks_path).parent)
    if not persist_dir:
        persist_dir = os.environ.get("CHROMA_PERSIST_DIR", "").strip() or "./data/chroma"
    meta_map = load_cases_meta(cases_path)

    if not base_url:
        base_url = os.environ.get("OLLAMA_BASE_URL", "").strip() or "http://localhost:11434"
    if not dry_run:
        if not model:
            model = os.environ.get("EMBEDDING_MODEL", "").strip()
        if not model:
            model = EXPECTED_OLLAMA_MODEL
        validate_collection_model(model)

    import chromadb
    client = chromadb.PersistentClient(path=persist_dir)
    base_meta = {
        "embedding_provider": "dry-run" if dry_run else "ollama",
        "model_name": (model if not dry_run else "pseudo-sha256"),
        "vector_dimension": 0,
        "normalize": "provider_default" if not dry_run else "l2",
        "distance_metric": "cosine",
        "collection_name": COLLECTION,
    }

    existing_ids = set()
    if resume:
        try:
            coll = client.get_collection(COLLECTION)
            existing_ids = set(coll.get(include=[])["ids"])
        except Exception:
            coll = client.create_collection(
                name=COLLECTION,
                metadata={"hnsw:space": "cosine", **base_meta},
            )
        else:
            current_meta = coll.metadata or {}
            current_model = current_meta.get("model_name")
            current_provider = current_meta.get("embedding_provider")
            if current_model and current_model != base_meta["model_name"]:
                raise SystemExit(
                    "已有 Chroma collection 的 embedding 模型是 "
                    f"{current_model}，当前模型是 {base_meta['model_name']}。"
                    "不同 embedding 模型不能用 --resume 写入同一 collection；请先重建索引。"
                )
            if current_provider and current_provider != base_meta["embedding_provider"]:
                raise SystemExit(
                    "已有 Chroma collection 的 embedding provider 是 "
                    f"{current_provider}，当前 provider 是 {base_meta['embedding_provider']}。"
                    "不同 embedding provider 不能用 --resume 写入同一 collection；请先重建索引。"
                )
            existing_dim = int(current_meta.get("vector_dimension") or 0)
            if existing_ids:
                try:
                    coll.get(limit=1, include=["embeddings"])
                except Exception as exc:
                    raise SystemExit(
                        "已有 Chroma collection 的向量索引文件不可读，不能安全 --resume。"
                        "请先清空 Chroma 持久化目录后从头重建索引。"
                    ) from exc
            coll.modify(metadata={**base_meta, "vector_dimension": existing_dim})
    else:
        # 同名 collection 若存在先删，保证可重复运行（同一模型版本内）
        try:
            client.delete_collection(COLLECTION)
        except Exception:
            pass
        coll = client.create_collection(
            name=COLLECTION,
            metadata={"hnsw:space": "cosine", **base_meta},
        )

    buf_ids, buf_docs, buf_meta, buf_txt = [], [], [], []
    total = 0
    seen = 0
    fixed_dim = None
    skipped_existing = 0
    dim_meta_written = False
    t0 = time.time()

    if existing_ids:
        existing_dim = int((coll.metadata or {}).get("vector_dimension") or 0)
        if existing_dim > 0:
            fixed_dim = existing_dim
            dim_meta_written = True

    def flush():
        nonlocal total, fixed_dim, dim_meta_written
        if not buf_txt:
            return
        if dry_run:
            vecs = embed_dryrun(buf_txt)
        else:
            vecs = embed_ollama_resilient(buf_txt, model, base_url)
        # 维度校验并固化（§4.1）
        d = len(vecs[0])
        if fixed_dim is None:
            fixed_dim = d
        elif d != fixed_dim:
            raise RuntimeError(f"向量维度不一致: {d} != {fixed_dim}")
        if not dim_meta_written:
            coll.modify(metadata={**base_meta, "vector_dimension": fixed_dim})
            dim_meta_written = True
        coll.add(ids=buf_ids, embeddings=vecs, documents=buf_docs, metadatas=buf_meta)
        total += len(buf_txt)
        buf_ids.clear(); buf_docs.clear(); buf_meta.clear(); buf_txt.clear()

    for ck in iter_chunks(chunks_path, limit):
        seen += 1
        if ck["chunk_id"] in existing_ids:
            skipped_existing += 1
            continue
        cmeta = meta_map.get(ck["case_id"], {})
        md = {
            "case_id": ck["case_id"],
            "chunk_id": ck["chunk_id"],
            "chunk_type": ck["chunk_type"],
            **cmeta,
        }
        buf_ids.append(ck["chunk_id"])
        buf_docs.append(ck["text"])
        buf_meta.append(md)
        buf_txt.append(ck["text"])
        if len(buf_txt) >= batch:
            flush()
    flush()

    report = {
        "collection": COLLECTION,
        "mode": "dry-run" if dry_run else "ollama",
        "model": (model if not dry_run else "pseudo-sha256"),
        "indexed_chunks": total,
        "chunks_seen": seen,
        "embedding_success_rate": round(((total + skipped_existing) / seen) * 100, 2) if seen else 0,
        "vector_dimension": fixed_dim,
        "distance_metric": "cosine",
        "persist_dir": persist_dir,
        "elapsed_sec": round(time.time() - t0, 2),
        "collection_count": coll.count(),
        "resume": resume,
        "skipped_existing": skipped_existing,
        "existing_before_resume": len(existing_ids),
    }
    if not dim_meta_written:
        coll.modify(metadata={**base_meta, "vector_dimension": fixed_dim or 0})
    return report


def smoke_query(persist_dir, dry_run=True, model=None, base_url=None):
    """冒烟自检：取一条已入库 chunk 的向量做近邻检索，确认能召回自身。"""
    import chromadb
    client = chromadb.PersistentClient(path=persist_dir)
    coll = client.get_collection(COLLECTION)
    got = coll.get(limit=1, include=["documents"])
    if not got["ids"]:
        return {"ok": False, "reason": "collection 为空"}
    qtext = got["documents"][0]
    if dry_run:
        qvec = embed_dryrun([qtext])[0]
    else:
        if not base_url:
            base_url = os.environ.get("OLLAMA_BASE_URL", "").strip() or "http://localhost:11434"
        if not model:
            model = os.environ.get("EMBEDDING_MODEL", "").strip() or EXPECTED_OLLAMA_MODEL
        validate_collection_model(model)
        qvec = embed_ollama_resilient([qtext], model, base_url)[0]
    res = coll.query(query_embeddings=[qvec], n_results=3,
                     include=["metadatas", "distances"])
    return {
        "ok": True,
        "query_chunk_id": got["ids"][0],
        "top1_chunk_id": res["ids"][0][0],
        "top1_distance": round(res["distances"][0][0], 6),
        "self_recall": got["ids"][0] == res["ids"][0][0],
        "top3_chunk_types": [m["chunk_type"] for m in res["metadatas"][0]],
    }


def query_smoke(persist_dir, dry_run=False, model=None, base_url=None, n_results=3):
    """5 条手工 query 验收：确认真实业务 query 能从 Chroma 返回候选。"""
    import chromadb
    client = chromadb.PersistentClient(path=persist_dir)
    coll = client.get_collection(COLLECTION)
    if dry_run:
        qvecs = embed_dryrun(QUERY_SMOKE_CASES)
        model_name = "pseudo-sha256"
    else:
        if not base_url:
            base_url = os.environ.get("OLLAMA_BASE_URL", "").strip() or "http://localhost:11434"
        if not model:
            model = os.environ.get("EMBEDDING_MODEL", "").strip() or EXPECTED_OLLAMA_MODEL
        validate_collection_model(model)
        model_name = model
        qvecs = embed_ollama_resilient(QUERY_SMOKE_CASES, model, base_url)
    res = coll.query(
        query_embeddings=qvecs,
        n_results=n_results,
        include=["documents", "metadatas", "distances"],
    )
    queries = []
    for i, query in enumerate(QUERY_SMOKE_CASES):
        top = []
        for rank, chunk_id in enumerate(res["ids"][i], 1):
            md = res["metadatas"][i][rank - 1]
            doc = (res["documents"][i][rank - 1] or "").replace("\n", " ")
            top.append({
                "rank": rank,
                "chunk_id": chunk_id,
                "case_id": md.get("case_id"),
                "chunk_type": md.get("chunk_type"),
                "case_cause": md.get("case_cause"),
                "court_level": md.get("court_level"),
                "trial_level": md.get("trial_level"),
                "judgment_year": md.get("judgment_year"),
                "region": md.get("region"),
                "distance": round(float(res["distances"][i][rank - 1]), 6),
                "snippet": doc[:120],
            })
        queries.append({"query": query, "returned": len(top), "top": top})
    return {
        "collection": COLLECTION,
        "collection_count": coll.count(),
        "metadata": coll.metadata,
        "mode": "dry-run" if dry_run else "ollama",
        "model": model_name,
        "queries": queries,
        "all_queries_returned_candidates": all(q["returned"] > 0 for q in queries),
    }


if __name__ == "__main__":
    _load_dotenv_fallback(Path.cwd())
    default_persist = os.environ.get("CHROMA_PERSIST_DIR", "").strip() or "./data/chroma"
    ap = argparse.ArgumentParser()
    ap.add_argument("--chunks", required=True)
    ap.add_argument("--cases", required=True)
    ap.add_argument("--persist", default=default_persist)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--model", default=None,
                    help="embedding 模型名，默认读 EMBEDDING_MODEL 或 bge-m3")
    ap.add_argument("--base-url", default=None,
                    help="Ollama 服务地址，默认读 OLLAMA_BASE_URL 或 http://localhost:11434")
    ap.add_argument("--batch", type=int, default=BATCH)
    ap.add_argument("--resume", action="store_true",
                    help="续跑：保留已有 collection，跳过已入库 chunk。")
    ap.add_argument("--smoke", action="store_true", help="入库后做近邻自检")
    ap.add_argument("--query-smoke", action="store_true",
                    help="入库后运行 5 条手工 query 验收")
    a = ap.parse_args()
    rep = run(a.chunks, a.cases, a.persist, a.limit, a.dry_run, a.model, a.batch,
              a.resume, a.base_url)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    if a.smoke:
        print("\n=== SMOKE QUERY ===")
        print(json.dumps(smoke_query(a.persist, a.dry_run, a.model, a.base_url), ensure_ascii=False, indent=2))
    if a.query_smoke:
        print("\n=== QUERY SMOKE ===")
        print(json.dumps(query_smoke(a.persist, a.dry_run, a.model, a.base_url), ensure_ascii=False, indent=2))
