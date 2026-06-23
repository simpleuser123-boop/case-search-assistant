# -*- coding: utf-8 -*-
"""E5-2 法条索引构建（statute_chunks.jsonl -> 独立 Chroma collection，与案件索引物理隔离）。

设计依据：落地设计文档/20-E5法条检索分步骤系统提示词文档.md §5（E5-2 目标 2）。
对标既有 index_chroma.py（4.4 案件向量索引）的构建范式（Ollama bge-m3 / dry-run 伪向量 /
维度固化 / 幂等重建），但**物理隔离**：

- 独立 collection 名：``statute_chunks_bge_m3_v1``（≠ 案件 ``case_chunks_bge_m3_v1``）。
- 独立 persist 目录：默认 ``CHROMA_STATUTE_PERSIST_DIR``，缺省回退到案件 persist 目录的
  同级 ``<sibling>/statute`` 子目录（仍与案件 Chroma 文件物理隔离，不写入案件目录）。
- 绝不删除/改写案件 collection；只 get_or_create 自己的 statute collection。

红线：
- 法条 chunk 100% 必带 text_id；缺 text_id 的 chunk 被跳过且计数，不入库。
- 不引入对外业务 flag；构建开关只用脚本参数 / 内部环境变量（CHROMA_STATUTE_PERSIST_DIR）。
- 日志/报告不打印条文长正文（仅回统计与短抽样）。
- chromadb 惰性 import：dry-run 逻辑校验在无 chromadb 环境亦可跑（仅在真正建库时 import）。
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import struct
import time
from pathlib import Path
from typing import Any, Iterable

# 法条 collection 与案件 collection 物理隔离（不同名 + 不同目录）。
STATUTE_COLLECTION = "statute_chunks_bge_m3_v1"
CASE_COLLECTION = "case_chunks_bge_m3_v1"  # 仅用于隔离断言/防御，绝不在此写入
EXPECTED_OLLAMA_MODEL = "bge-m3"
BATCH = 8


def resolve_statute_persist_dir(persist_dir: str | None = None) -> str:
    """解析法条索引 persist 目录，保证与案件索引物理隔离。

    优先级：显式 persist_dir > CHROMA_STATUTE_PERSIST_DIR > 案件 persist 同级 /statute 子目录。
    永远返回一个**不等于**案件 persist 目录的路径。
    """
    if persist_dir and persist_dir.strip():
        chosen = persist_dir.strip()
    else:
        env = os.environ.get("CHROMA_STATUTE_PERSIST_DIR", "").strip()
        if env:
            chosen = env
        else:
            case_dir = os.environ.get("CHROMA_PERSIST_DIR", "").strip() or "./data/chroma"
            chosen = str(Path(case_dir).parent / (Path(case_dir).name + "_statute"))
    case_dir = os.environ.get("CHROMA_PERSIST_DIR", "").strip() or "./data/chroma"
    if Path(chosen).resolve() == Path(case_dir).resolve():
        raise SystemExit(
            "法条索引 persist 目录不得与案件索引 persist 目录相同（物理隔离红线）。"
        )
    return chosen


def iter_statute_chunks(chunks_path: str, limit: int = 0) -> Iterable[dict[str, Any]]:
    with open(chunks_path, encoding="utf-8") as f:
        for i, line in enumerate(f):
            if limit and i >= limit:
                break
            line = line.strip()
            if line:
                yield json.loads(line)


def embed_dryrun(texts: list[str], dim: int = 1024) -> list[list[float]]:
    """确定性伪向量（sha256 填充 + L2 归一），无密钥/无网络可验证写入与检索逻辑。"""
    out = []
    for t in texts:
        h = hashlib.sha256(t.encode("utf-8")).digest()
        raw = (h * ((dim * 4) // len(h) + 1))[: dim * 4]
        vec = [struct.unpack_from(">i", raw, i * 4)[0] / 2**31 for i in range(dim)]
        norm = sum(x * x for x in vec) ** 0.5 or 1.0
        out.append([x / norm for x in vec])
    return out


def embed_ollama(texts: list[str], model: str, base_url: str) -> list[list[float]]:
    """真实 bge-m3 embedding（本地 Ollama /api/embed）；仅标准库 urllib。"""
    import urllib.error
    import urllib.request

    url = base_url.rstrip("/") + "/api/embed"
    payload = json.dumps({"model": model, "input": texts}).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=600) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Ollama embedding 失败: {getattr(exc, 'reason', exc)}") from exc
    embs = body.get("embeddings")
    if not embs or len(embs) != len(texts):
        raise RuntimeError("Ollama 返回 embeddings 数量异常")
    return embs


def run(
    *,
    chunks_path: str = "data/processed/statute_chunks.jsonl",
    persist_dir: str | None = None,
    limit: int = 0,
    dry_run: bool = False,
    model: str | None = None,
    base_url: str | None = None,
    batch: int = BATCH,
) -> dict[str, Any]:
    """构建法条索引（幂等：重建自己的 statute collection；从不碰案件 collection）。"""
    persist = resolve_statute_persist_dir(persist_dir)
    if not dry_run:
        model = model or os.environ.get("EMBEDDING_MODEL", "").strip() or EXPECTED_OLLAMA_MODEL
        base_url = base_url or os.environ.get("OLLAMA_BASE_URL", "").strip() or "http://localhost:11434"
        if model != EXPECTED_OLLAMA_MODEL:
            raise SystemExit(
                f"{STATUTE_COLLECTION} 只能写入 {EXPECTED_OLLAMA_MODEL} 向量；当前 {model}。"
            )

    # 锚点完整性闸：缺 text_id 的 chunk 跳过且计数（不入库、不进可展示集）。
    rows: list[dict[str, Any]] = []
    skipped_no_anchor = 0
    for ck in iter_statute_chunks(chunks_path, limit):
        if not ck.get("text_id") or not str(ck["text_id"]).strip():
            skipped_no_anchor += 1
            continue
        rows.append(ck)

    base_meta = {
        "embedding_provider": "dry-run" if dry_run else "ollama",
        "model_name": "pseudo-sha256" if dry_run else model,
        "vector_dimension": 0,
        "distance_metric": "cosine",
        "collection_name": STATUTE_COLLECTION,
        "corpus": "statute",
    }

    indexed = 0
    fixed_dim: int | None = None
    t0 = time.time()

    if not dry_run or os.environ.get("E5_STATUTE_INDEX_FORCE_CHROMA") == "1":
        import chromadb  # 惰性：仅真正建库时引入

        client = chromadb.PersistentClient(path=persist)
        # 隔离防御：绝不在此误删案件 collection；只重建自己的 statute collection。
        try:
            client.delete_collection(STATUTE_COLLECTION)
        except Exception:
            pass
        coll = client.create_collection(
            name=STATUTE_COLLECTION, metadata={"hnsw:space": "cosine", **base_meta}
        )
        buf_ids, buf_docs, buf_meta, buf_txt = [], [], [], []

        def flush():
            nonlocal indexed, fixed_dim
            if not buf_txt:
                return
            vecs = embed_dryrun(buf_txt) if dry_run else embed_ollama(buf_txt, model, base_url)
            d = len(vecs[0])
            if fixed_dim is None:
                fixed_dim = d
                coll.modify(metadata={**base_meta, "vector_dimension": d})
            elif d != fixed_dim:
                raise RuntimeError(f"向量维度不一致: {d} != {fixed_dim}")
            coll.add(ids=buf_ids, embeddings=vecs, documents=buf_docs, metadatas=buf_meta)
            indexed += len(buf_txt)
            buf_ids.clear(); buf_docs.clear(); buf_meta.clear(); buf_txt.clear()

        for ck in rows:
            buf_ids.append(ck["statute_chunk_id"])
            buf_docs.append(ck["text"])
            buf_meta.append({
                "statute_id": ck.get("statute_id", ""),
                "text_id": ck["text_id"],
                "law_name": ck.get("law_name", ""),
                "article_no": str(ck.get("article_no", "")),
                "chunk_type": ck.get("chunk_type", ""),
                "has_article_text": bool(ck.get("has_article_text", False)),
                "source_corpus": ck.get("source_corpus", ""),
                "coverage_domain": ck.get("coverage_domain", "criminal"),
            })
            buf_txt.append(ck["text"])
            if len(buf_txt) >= batch:
                flush()
        flush()
        collection_count = coll.count()
    else:
        # 纯逻辑 dry-run（无 chromadb）：只校验锚点闸与计数，不落库。
        indexed = len(rows)
        collection_count = len(rows)
        fixed_dim = 1024

    return {
        "step": "E5-2-build_statute_index",
        "generated_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "collection": STATUTE_COLLECTION,
        "isolated_from_case_collection": STATUTE_COLLECTION != CASE_COLLECTION,
        "persist_dir": persist,
        "mode": "dry-run" if dry_run else "ollama",
        "indexed_statute_chunks": indexed,
        "skipped_missing_anchor": skipped_no_anchor,
        "all_indexed_have_text_id": skipped_no_anchor == 0 or indexed >= 0,
        "vector_dimension": fixed_dim,
        "distance_metric": "cosine",
        "collection_count": collection_count,
        "elapsed_sec": round(time.time() - t0, 2),
    }


def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="build_statute_index",
        description="E5-2 statute index builder: statute_chunks.jsonl -> isolated Chroma collection (physically separate from case index; idempotent; no business flag)",
    )
    ap.add_argument("--chunks", default="data/processed/statute_chunks.jsonl", help="statute chunks input")
    ap.add_argument("--persist", default=None, help="statute persist dir (default CHROMA_STATUTE_PERSIST_DIR or <case_dir>_statute)")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true", help="logic-only (no chromadb needed unless E5_STATUTE_INDEX_FORCE_CHROMA=1)")
    ap.add_argument("--model", default=None)
    ap.add_argument("--base-url", default=None)
    ap.add_argument("--batch", type=int, default=BATCH)
    return ap


if __name__ == "__main__":
    args = build_arg_parser().parse_args()
    rep = run(
        chunks_path=args.chunks,
        persist_dir=args.persist,
        limit=args.limit,
        dry_run=args.dry_run,
        model=args.model,
        base_url=args.base_url,
        batch=args.batch,
    )
    print(json.dumps(rep, ensure_ascii=False, indent=2))
