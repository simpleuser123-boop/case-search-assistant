# -*- coding: utf-8 -*-
"""E5-2 法条语料与标注管道单测（小样本 + 锚点完整性 + 不改案件产物 + 索引隔离）。

设计依据：落地设计文档/20-E5法条检索分步骤系统提示词文档.md §5（E5-2 测试要求）。

测试纪律（沿用 E5 共用约束 §3 / doc20 line 123）：
- fixture 只用**短假法条片段 / 假 case_id / 假 text_id**，绝不写真实长正文型数据。
- 管道模块按**文件路径 importlib 加载**，不触发 app.kernel.__init__ 重依赖链，
  使本测试在 VM（无 pydantic/chromadb）与 host .venv311 均可跑。
- 断言只读案件产物、未修改案件 cases.jsonl/chunks.jsonl（hash/mtime 不变）。
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

_PIPELINE_DIR = Path(__file__).resolve().parents[1] / "app" / "kernel" / "data" / "pipeline"


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, _PIPELINE_DIR / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


corpus = _load("e5_build_statute_corpus", "build_statute_corpus.py")
links = _load("e5_build_case_statute_links", "build_case_statute_links.py")
index = _load("e5_build_statute_index", "build_statute_index.py")


# 短合成法条 fixture（假 text_id + 短条文，绝非真实长正文）。
FAKE_SEED = [
    {"text_id": "fake::cl::art_133", "name": "中华人民共和国刑法", "text": "第一百三十三条 测试用合成条文一。", "article_no": 133},
    {"text_id": "fake::cl::art_264", "name": "中华人民共和国刑法", "text": "第二百六十四条 测试用合成条文二。", "article_no": 264},
    {"text_id": "", "name": "中华人民共和国刑法", "text": "缺锚点条文应被丢弃。", "article_no": 999},  # 缺 text_id
]


# ---------- 1. 小样本：规整后字段齐全 + 条文未被改写 ----------

def test_seed_normalize_fields_and_text_preserved():
    a = corpus.normalize_seed_record(FAKE_SEED[0])
    assert a is not None
    for key in ("statute_id", "law_name", "article_no", "text_id"):
        assert a[key], f"missing {key}"
    # 条文原样保留，未被改写/补全
    assert a["article_text"] == FAKE_SEED[0]["text"]
    assert a["source_corpus"] == corpus.SOURCE_CORPUS_SEED
    assert a["statute_id"] == "cn_criminal_law_art_133"


def test_seed_missing_text_id_dropped():
    # 缺 text_id 的种子记录被丢弃（返回 None），不进可展示集
    assert corpus.normalize_seed_record(FAKE_SEED[2]) is None


# ---------- 2. 锚点完整性：chunk 100% 带 text_id；缺锚点不入展示集 ----------

def test_every_statute_chunk_has_text_id():
    for rec in FAKE_SEED[:2]:
        s = corpus.normalize_seed_record(rec)
        ck = corpus.build_statute_chunk(s)
        assert ck is not None
        assert ck["text_id"], "statute chunk must carry text_id anchor"


def test_chunk_dropped_when_anchor_missing():
    broken = {"statute_id": "x", "law_name": "L", "article_no": "1", "text_id": "", "article_text": "t"}
    assert corpus.build_statute_chunk(broken) is None


def test_seed_chunk_text_equals_corpus_text_not_rewritten():
    s = corpus.normalize_seed_record(FAKE_SEED[0])
    ck = corpus.build_statute_chunk(s)
    # seed 模式 chunk 文本 == 语料条文（原样，未改写）
    assert ck["chunk_type"] == "statute_article"
    assert ck["text"] == FAKE_SEED[0]["text"]
    assert ck["has_article_text"] is True


# ---------- 3. catalog 模式：article_text 为空，绝不杜撰条文 ----------

def test_catalog_mode_no_fabricated_article_text():
    statutes = corpus.build_catalog_statutes([133, "第264条", 133, 5], law_name=corpus.DEFAULT_LAW_NAME)
    sids = {s["statute_id"] for s in statutes}
    assert "cn_criminal_law_art_133" in sids
    assert "cn_criminal_law_art_264" in sids
    # 去重：133 只出现一次
    assert sum(1 for s in statutes if s["statute_id"] == "cn_criminal_law_art_133") == 1
    for s in statutes:
        assert s["article_text"] is None, "catalog mode must not fabricate article_text"
        assert s["text_id"], "catalog statute must still carry a text_id anchor"
        assert s["source_corpus"] == corpus.SOURCE_CORPUS_CATALOG


def test_catalog_chunk_label_is_structured_not_body():
    statutes = corpus.build_catalog_statutes([133], law_name=corpus.DEFAULT_LAW_NAME)
    ck = corpus.build_statute_chunk(statutes[0])
    assert ck["chunk_type"] == "statute_label"
    assert ck["has_article_text"] is False
    # label 仅为「法名+第X条」结构化标识，非裁判正文/合成条文
    assert ck["text"] == "中华人民共和国刑法第133条"


def test_statute_id_stable_across_seed_and_catalog():
    # seed 与 catalog 对同一条号派生相同 statute_id（保证可合流 join）
    seed_id = corpus.normalize_seed_record(FAKE_SEED[0])["statute_id"]
    cat_id = corpus.build_catalog_statutes([133])[0]["statute_id"]
    assert seed_id == cat_id == "cn_criminal_law_art_133"


# ---------- 4. 类案->法条关联标注：映射带 text_id；qrels 不进运行时 ----------

def test_case_statute_link_mapping_has_text_id():
    link = links.build_links_for_case("fake_case_001", [133, 264, 133])
    assert link is not None
    assert link["case_id"] == "fake_case_001"
    # 去重 133；每个 ref 带 statute_id + text_id 锚点
    sids = [r["statute_id"] for r in link["statute_refs"]]
    assert sids == sorted(set(sids), key=lambda s: int(s.rsplit("_", 1)[1]))
    for r in link["statute_refs"]:
        assert r["text_id"], "each statute ref must carry text_id anchor"
        assert r["statute_id"]


def test_case_with_no_articles_yields_no_link():
    assert links.build_links_for_case("fake_case_empty", []) is None


def test_links_run_does_not_use_qrels(tmp_path):
    fixture = tmp_path / "cases.jsonl"
    fixture.write_text(
        json.dumps({"case_id": "fc1", "law_articles": [133, 264]}, ensure_ascii=False) + "\n"
        + json.dumps({"case_id": "fc2", "law_articles": [5]}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    rep, _ = links.run(cases_path=str(fixture), out_dir=str(tmp_path), dry_run=False)
    assert rep["qrels_used"] is False
    assert rep["reads_case_products_only"] is True
    assert rep["modifies_case_products"] is False
    assert rep["cases_with_links"] == 2
    out = tmp_path / "case_statute_links.jsonl"
    assert out.is_file()


# ---------- 5. 法条索引与案件索引物理隔离 ----------

def test_statute_collection_isolated_from_case_collection():
    assert index.STATUTE_COLLECTION != index.CASE_COLLECTION
    assert index.STATUTE_COLLECTION == "statute_chunks_bge_m3_v1"


def test_statute_persist_dir_never_equals_case_dir(monkeypatch):
    monkeypatch.setenv("CHROMA_PERSIST_DIR", str(Path("/tmp/case_chroma")))
    monkeypatch.delenv("CHROMA_STATUTE_PERSIST_DIR", raising=False)
    resolved = index.resolve_statute_persist_dir(None)
    assert Path(resolved).resolve() != Path("/tmp/case_chroma").resolve()


def test_statute_persist_dir_rejects_equal_to_case_dir(monkeypatch):
    monkeypatch.setenv("CHROMA_PERSIST_DIR", str(Path("/tmp/case_chroma")))
    with pytest.raises(SystemExit):
        index.resolve_statute_persist_dir("/tmp/case_chroma")


def test_index_run_dryrun_skips_missing_anchor(tmp_path):
    chunks = tmp_path / "statute_chunks.jsonl"
    chunks.write_text(
        json.dumps({"statute_chunk_id": "a", "statute_id": "s1", "text_id": "fake::a", "text": "刑法第1条", "law_name": "L", "article_no": "1", "chunk_type": "statute_label"}, ensure_ascii=False) + "\n"
        + json.dumps({"statute_chunk_id": "b", "statute_id": "s2", "text_id": "", "text": "无锚点", "law_name": "L", "article_no": "2", "chunk_type": "statute_label"}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    monkeypatch_dir = tmp_path / "case_chroma"  # noqa: F841 (kept for readability)
    rep = index.run(chunks_path=str(chunks), persist_dir=str(tmp_path / "statute_idx"), dry_run=True)
    assert rep["indexed_statute_chunks"] == 1  # 缺锚点的被跳过
    assert rep["skipped_missing_anchor"] == 1
    assert rep["isolated_from_case_collection"] is True


# ---------- 6. 只读案件产物：运行管道后真实 cases.jsonl/chunks.jsonl hash/mtime 不变 ----------

def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def test_real_case_products_untouched_after_pipeline(tmp_path):
    repo = Path(__file__).resolve().parents[3]
    cases = repo / "data" / "processed" / "cases.jsonl"
    chunks = repo / "data" / "processed" / "chunks.jsonl"
    if not cases.is_file():
        pytest.skip("real cases.jsonl absent in this environment")
    before = {p: (_sha(p), p.stat().st_mtime_ns) for p in (cases, chunks) if p.is_file()}
    # 运行 catalog 语料 + 关联标注，输出到 tmp（绝不写回案件产物）
    corpus.run(mode="catalog", cases_path=str(cases), out_dir=str(tmp_path), dry_run=False)
    links.run(cases_path=str(cases), out_dir=str(tmp_path), dry_run=False)
    for p, (h, m) in before.items():
        assert _sha(p) == h, f"{p.name} content changed!"
        assert p.stat().st_mtime_ns == m, f"{p.name} mtime changed!"


# ---------- 7. 无裁判正文/PII 泄露进法条产物 ----------

FORBIDDEN_SUBSTRINGS = ["被告人", "判处", "本院认为", "身份证", "住址", "手机号"]


def test_generated_statute_products_have_no_body_or_pii(tmp_path):
    # 用合成种子产物做泄露扫描
    seed_file = tmp_path / "seed.jsonl"
    seed_file.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in FAKE_SEED[:2]), encoding="utf-8")
    corpus.run(mode="seed", seed_path=str(seed_file), out_dir=str(tmp_path), dry_run=False)
    for name in ("statutes.jsonl", "statute_chunks.jsonl"):
        text = (tmp_path / name).read_text(encoding="utf-8")
        for bad in FORBIDDEN_SUBSTRINGS:
            assert bad not in text, f"forbidden body/PII token {bad!r} leaked into {name}"


def test_statute_record_keys_are_whitelist_only(tmp_path):
    seed_file = tmp_path / "seed.jsonl"
    seed_file.write_text(json.dumps(FAKE_SEED[0], ensure_ascii=False), encoding="utf-8")
    corpus.run(mode="seed", seed_path=str(seed_file), out_dir=str(tmp_path), dry_run=False)
    allowed = {"statute_id", "law_name", "article_no", "text_id", "article_text", "source_corpus", "effective_status", "coverage_domain"}
    for line in (tmp_path / "statutes.jsonl").read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        assert set(rec.keys()) <= allowed, f"unexpected keys: {set(rec.keys()) - allowed}"

