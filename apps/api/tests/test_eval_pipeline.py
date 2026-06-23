import json
from pathlib import Path

from app.eval.product_eval import evaluate_product, load_product_qrels, ndcg_at, precision_at, top_k_has_hit
from app.eval.bm25_baseline import evaluate
from app.eval.day3_rerank_eval import (
    evaluate_rerank_over_lecardv2_bm25_pool,
    run_product_smoke,
)
from app.eval.prepare_lecardv2_eval import prepare
from app.retrieval.models import VectorCandidate, VectorRetrievalResult
from scripts.eval_corpus_preflight import build_report as build_eval_corpus_preflight_report


def write_jsonl(path: Path, rows: list[dict]):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def test_prepare_lecardv2_eval_standardizes_queries_and_qrels(tmp_path):
    root = tmp_path / "LeCaRDv2-main"
    write_jsonl(root / "query" / "test_query.json", [
        {"id": 1, "query": "全文一", "fact": "事实一"},
        {"id": 2, "query": "全文二", "fact": "事实二"},
    ])
    write_jsonl(root / "query" / "common_query.json", [{"id": 1, "query": "全文一", "fact": "事实一"}])
    write_jsonl(root / "query" / "controversial_query.json", [])
    write_jsonl(root / "query" / "procedural_query.json", [{"id": 2, "query": "全文二", "fact": "事实二"}])
    (root / "label").mkdir(parents=True)
    (root / "label" / "test_relevence.trec").write_text(
        "1\t0\t101\t3\n1\t0\t102\t1\n2\t0\t201\t2\n",
        encoding="utf-8",
    )
    (root / "candidate").mkdir()

    out_dir = tmp_path / "eval"
    report = prepare(root, out_dir, split="test")

    queries = [json.loads(line) for line in (out_dir / "lecardv2_queries.jsonl").read_text(encoding="utf-8").splitlines()]
    qrels = [json.loads(line) for line in (out_dir / "lecardv2_qrels.jsonl").read_text(encoding="utf-8").splitlines()]

    assert report["query_count"] == 2
    assert report["qrels_count"] == 3
    assert report["queries_with_relevant_labels"] == 2
    assert report["candidate_corpus"]["found"] is False
    assert queries[0]["eval_query_id"] == "lecardv2_q1"
    assert queries[0]["query_text"] == "事实一"
    assert queries[1]["query_type"] == "procedural"
    assert qrels[0]["candidate_case_id"] == "101"
    assert qrels[0]["is_relevant"] is True


def test_bm25_baseline_outputs_metrics_when_corpus_exists(tmp_path):
    queries = tmp_path / "queries.jsonl"
    qrels = tmp_path / "qrels.jsonl"
    corpus = tmp_path / "corpus.jsonl"
    out = tmp_path / "report.json"

    write_jsonl(queries, [
        {"eval_query_id": "q1", "source_query_id": "1", "query_text": "危险废物 倾倒 污染环境"},
    ])
    write_jsonl(qrels, [
        {"eval_query_id": "q1", "candidate_case_id": "doc-good", "relevance": 3},
        {"eval_query_id": "q1", "candidate_case_id": "doc-bad", "relevance": 0},
    ])
    write_jsonl(corpus, [
        {"pid": "doc-good", "fact": "被告人非法倾倒危险废物，构成污染环境罪。"},
        {"pid": "doc-bad", "fact": "被告人盗窃他人财物。"},
    ])

    report = evaluate(queries, qrels, corpus, out)

    assert report["status"] == "ok"
    assert report["precision_at_5"] > 0
    assert report["ndcg_at_10"] > 0
    assert report["per_query"][0]["top10"][0]["candidate_case_id"] == "doc-good"


def test_bm25_baseline_reports_missing_candidate_corpus(tmp_path):
    queries = tmp_path / "queries.jsonl"
    qrels = tmp_path / "qrels.jsonl"
    out = tmp_path / "report.json"
    write_jsonl(queries, [{"eval_query_id": "q1", "source_query_id": "1", "query_text": "抢劫"}])
    write_jsonl(qrels, [{"eval_query_id": "q1", "candidate_case_id": "101", "relevance": 3}])

    report = evaluate(queries, qrels, tmp_path / "missing-corpus", out)

    assert report["status"] == "blocked_missing_candidate_corpus"
    assert "candidate case texts were not found" in report["reason"]


def test_day3_rerank_eval_outputs_comparable_metrics_and_bad_cases(tmp_path):
    queries = tmp_path / "queries.jsonl"
    qrels = tmp_path / "qrels.jsonl"
    corpus = tmp_path / "corpus.jsonl"

    write_jsonl(queries, [
        {"eval_query_id": "q1", "source_query_id": "1", "query_text": "危险废物 倾倒 污染环境"},
        {"eval_query_id": "q2", "source_query_id": "2", "query_text": "抢劫 刀 抢钱"},
    ])
    write_jsonl(qrels, [
        {"eval_query_id": "q1", "candidate_case_id": "doc-good-1", "relevance": 3},
        {"eval_query_id": "q1", "candidate_case_id": "doc-bad-1", "relevance": 0},
        {"eval_query_id": "q2", "candidate_case_id": "doc-good-2", "relevance": 3},
    ])
    write_jsonl(corpus, [
        {"pid": "doc-good-1", "fact": "被告人非法倾倒危险废物，构成污染环境罪。"},
        {"pid": "doc-bad-1", "fact": "被告人盗窃他人财物。"},
        {"pid": "doc-good-2", "fact": "被告人持刀抢劫，强行劫取他人财物。"},
    ])

    report = evaluate_rerank_over_lecardv2_bm25_pool(
        queries_path=queries,
        qrels_path=qrels,
        corpus_path=corpus,
        candidate_pool_k=10,
        enable_query_rewrite=False,
    )

    assert report["status"] == "ok"
    assert report["comparable_with_lecardv2_qrels"] is True
    assert report["baseline_pool_metrics"]["evaluated_query_count"] == 2
    assert report["current_rerank_metrics"]["evaluated_query_count"] == 2
    assert "precision_at_5" in report["metric_delta"]
    assert len(report["per_query"]) == 2
    assert all("baseline_top10" in row and "current_top10" in row for row in report["per_query"])
    assert isinstance(report["bad_cases"], list)


def test_day3_product_smoke_reports_dataset_mismatch_when_not_comparable(tmp_path):
    queries = tmp_path / "queries.jsonl"
    qrels = tmp_path / "qrels.jsonl"
    write_jsonl(queries, [
        {"eval_query_id": "q1", "source_query_id": "1", "query_text": "污染环境"},
    ])
    write_jsonl(qrels, [
        {"eval_query_id": "q1", "candidate_case_id": "lecard-doc-1", "relevance": 3},
    ])

    report = run_product_smoke(
        queries_path=queries,
        qrels_path=qrels,
        limit_queries=0,
        enable_query_rewrite=False,
    )

    assert report["status"] == "skipped"
    assert report["comparable_with_lecardv2_qrels"] is False


def test_eval_corpus_preflight_checks_lecard_and_product_overlap(tmp_path):
    lecard_queries = tmp_path / "lecard_queries.jsonl"
    lecard_qrels = tmp_path / "lecard_qrels.jsonl"
    lecard_corpus = tmp_path / "lecard_corpus.jsonl"
    product_queries = tmp_path / "product_queries.jsonl"
    product_qrels = tmp_path / "product_qrels.jsonl"
    product_cases = tmp_path / "cases.jsonl"
    product_chunks = tmp_path / "chunks.jsonl"

    write_jsonl(lecard_queries, [{"eval_query_id": "lq1", "query_text": "污染环境"}])
    write_jsonl(lecard_qrels, [{"eval_query_id": "lq1", "candidate_case_id": "doc-good", "relevance": 3}])
    write_jsonl(lecard_corpus, [{"pid": "doc-good", "fact": "非法倾倒危险废物，构成污染环境罪。"}])

    write_jsonl(product_queries, [
        {"eval_query_id": f"pq{i:02d}", "query_text": "盗窃 财物"}
        for i in range(1, 21)
    ])
    write_jsonl(product_qrels, [
        {"eval_query_id": f"pq{i:02d}", "candidate_case_id": "case-good", "relevance": 3}
        for i in range(1, 11)
    ])
    write_jsonl(product_cases, [{"case_id": "case-good", "case_cause": "盗窃罪"}])
    write_jsonl(product_chunks, [{"case_id": "case-good", "chunk_id": "c1", "text": "盗窃他人财物。"}])

    report = build_eval_corpus_preflight_report(
        lecard_queries=lecard_queries,
        lecard_qrels=lecard_qrels,
        lecard_corpus=lecard_corpus,
        product_queries=product_queries,
        product_qrels=product_qrels,
        product_cases=product_cases,
        product_chunks=product_chunks,
    )

    assert report["status"] == "ok"
    assert report["lecardv2"]["candidate_corpus"]["doc_count"] == 1
    assert report["lecardv2"]["id_overlap"]["overlap_count"] == 1
    assert report["product_local"]["queries"]["query_count"] == 20
    assert report["product_local"]["qrels"]["relevant_query_count"] == 10
    assert report["product_local"]["id_overlap"]["overlap_count"] == 1
    assert report["privacy"]["raw_query_text_written"] is False


def test_eval_corpus_preflight_blocks_missing_lecard_candidate_corpus(tmp_path):
    lecard_queries = tmp_path / "lecard_queries.jsonl"
    lecard_qrels = tmp_path / "lecard_qrels.jsonl"
    product_queries = tmp_path / "product_queries.jsonl"
    product_qrels = tmp_path / "product_qrels.jsonl"
    product_cases = tmp_path / "cases.jsonl"
    product_chunks = tmp_path / "chunks.jsonl"

    write_jsonl(lecard_queries, [{"eval_query_id": "lq1", "query_text": "污染环境"}])
    write_jsonl(lecard_qrels, [{"eval_query_id": "lq1", "candidate_case_id": "doc-good", "relevance": 3}])
    write_jsonl(product_queries, [
        {"eval_query_id": f"pq{i:02d}", "query_text": "盗窃 财物"}
        for i in range(1, 21)
    ])
    write_jsonl(product_qrels, [
        {"eval_query_id": f"pq{i:02d}", "candidate_case_id": "case-good", "relevance": 3}
        for i in range(1, 11)
    ])
    write_jsonl(product_cases, [{"case_id": "case-good", "case_cause": "盗窃罪"}])
    write_jsonl(product_chunks, [{"case_id": "case-good", "chunk_id": "c1", "text": "盗窃他人财物。"}])

    report = build_eval_corpus_preflight_report(
        lecard_queries=lecard_queries,
        lecard_qrels=lecard_qrels,
        lecard_corpus=tmp_path / "missing-candidate",
        product_queries=product_queries,
        product_qrels=product_qrels,
        product_cases=product_cases,
        product_chunks=product_chunks,
    )

    assert report["status"] == "blocked"
    assert report["lecardv2"]["status"] == "blocked"
    assert "candidate_corpus_path_missing" in report["lecardv2"]["errors"]
    assert report["product_local"]["status"] == "ok"


def test_product_eval_metric_helpers():
    rels = {"case-good": 3, "case-ok": 2, "case-bad": 0}
    ranked = ["case-good", "case-bad", "case-ok"]

    assert precision_at(ranked, rels, k=5) == 0.4
    assert ndcg_at(ranked, rels, k=10) > 0
    assert top_k_has_hit(ranked, rels, k=10) is True


class FakeProductRetrievalService:
    def retrieve(self, query_plan, *, include_relaxed_recall: bool = False):
        return VectorRetrievalResult(
            candidates=[
                VectorCandidate(
                    case_id="case-good",
                    chunk_id="case-good-c1",
                    vector_score=0.95,
                    retrieval_source="original_vector",
                    metadata={
                        "case_id": "case-good",
                        "chunk_id": "case-good-c1",
                        "case_cause": "盗窃罪",
                    },
                    matched_text="盗窃他人财物。",
                    source="fake",
                    retrieval_score=0.95,
                ),
                VectorCandidate(
                    case_id="case-bad",
                    chunk_id="case-bad-c1",
                    vector_score=0.5,
                    retrieval_source="original_vector",
                    metadata={
                        "case_id": "case-bad",
                        "chunk_id": "case-bad-c1",
                        "case_cause": "诈骗罪",
                    },
                    matched_text="虚构事实骗取财物。",
                    source="fake",
                    retrieval_score=0.5,
                ),
            ],
            retrieval_duration_ms=1,
            embedding_duration_ms=1,
            degraded=False,
            degraded_reasons=[],
        )


def test_product_eval_outputs_metrics_and_sanitized_bad_case_report(tmp_path):
    queries = tmp_path / "queries.jsonl"
    qrels = tmp_path / "qrels.jsonl"
    cases = tmp_path / "cases.jsonl"
    chunks = tmp_path / "chunks.jsonl"
    out = tmp_path / "product_eval_report.json"
    bad_cases = tmp_path / "bad_cases_product_eval.json"

    write_jsonl(queries, [
        {"eval_query_id": f"pq{i:02d}", "query_text": "盗窃 财物"}
        for i in range(1, 21)
    ])
    write_jsonl(qrels, [
        {"eval_query_id": f"pq{i:02d}", "candidate_case_id": "case-good", "relevance": 3}
        for i in range(1, 11)
    ])
    write_jsonl(cases, [
        {"case_id": "case-good", "case_cause": "盗窃罪"},
        {"case_id": "case-bad", "case_cause": "诈骗罪"},
    ])
    write_jsonl(chunks, [
        {"case_id": "case-good", "chunk_id": "case-good-c1", "text": "盗窃他人财物。"},
        {"case_id": "case-bad", "chunk_id": "case-bad-c1", "text": "虚构事实骗取财物。"},
    ])

    report = evaluate_product(
        queries_path=queries,
        qrels_path=qrels,
        cases_path=cases,
        chunks_path=chunks,
        output_path=out,
        bad_cases_path=bad_cases,
        retrieval_service=FakeProductRetrievalService(),
    )

    assert report["eval_set"]["query_count"] == 20
    assert report["eval_set"]["labeled_query_count"] == 10
    assert report["baseline"]["evaluated_query_count"] == 10
    assert report["current"]["evaluated_query_count"] == 10
    assert "precision_at_5" in report["metric_delta"]
    assert report["feature_flags"]["feature_flag_changed"] is False
    assert report["m13_regression_gate"]["grayCandidateHardGatePassed"] is False
    assert report["gray_candidate"]["weightedRerankGrayCandidate"] == report["gray_candidate"][
        "grayCandidateHardGatePassed"
    ]
    assert report["gray_candidate"]["weightedRerankGrayCandidate"] is False
    assert len(report["unified_results"]) == 2
    assert report["unified_results"][0]["evalLine"] == "product_local"
    assert report["unified_results"][0]["Precision@5"] == report["baseline"]["precision_at_5"]
    assert out.exists()
    assert bad_cases.exists()
    assert "盗窃 财物" not in out.read_text(encoding="utf-8")
    assert "盗窃 财物" not in bad_cases.read_text(encoding="utf-8")
    assert load_product_qrels(qrels)["pq01"]["case-good"] == 3


def test_product_eval_bm25_pool_rerank_uses_same_candidate_pool(tmp_path):
    queries = tmp_path / "queries.jsonl"
    qrels = tmp_path / "qrels.jsonl"
    cases = tmp_path / "cases.jsonl"
    chunks = tmp_path / "chunks.jsonl"
    out = tmp_path / "product_eval_report.json"
    bad_cases = tmp_path / "bad_cases_product_eval.json"

    write_jsonl(queries, [
        {"eval_query_id": f"pq{i:02d}", "query_text": "盗窃 财物", "version": "test_product_eval"}
        for i in range(1, 21)
    ])
    write_jsonl(qrels, [
        {"eval_query_id": f"pq{i:02d}", "candidate_case_id": "case-good", "relevance": 3}
        for i in range(1, 21)
    ])
    write_jsonl(cases, [
        {"case_id": "case-good", "case_cause": "盗窃罪"},
        {"case_id": "case-bad", "case_cause": "诈骗罪"},
    ])
    write_jsonl(chunks, [
        {
            "case_id": "case-good",
            "chunk_id": "case-good-c1",
            "chunk_type": "fact",
            "text": "盗窃他人财物。",
        },
        {
            "case_id": "case-bad",
            "chunk_id": "case-bad-c1",
            "chunk_type": "fact",
            "text": "虚构事实骗取财物。",
        },
    ])

    report = evaluate_product(
        queries_path=queries,
        qrels_path=qrels,
        cases_path=cases,
        chunks_path=chunks,
        output_path=out,
        bad_cases_path=bad_cases,
        comparison_mode="bm25_pool_rerank",
    )

    assert report["modes"]["comparison_mode"] == "bm25_pool_rerank"
    assert report["modes"]["same_candidate_pool_required"] is True
    assert report["candidate_set_summary"]["same_candidate_pool_query_count"] == 20
    assert report["candidate_set_summary"]["different_candidate_pool_query_count"] == 0
    assert all(row["candidate_set"]["same_candidate_ids"] is True for row in report["per_query"] if row["evaluated"])
    assert report["unified_results"][1]["mode"] == "current"
    assert report["unified_results"][1]["candidateCorpus"]["candidateSet"].startswith("per-query BM25")
    assert report["feature_flags"]["feature_flag_changed"] is False
    assert report["gray_candidate"]["weightedRerankGrayCandidate"] == report["gray_candidate"][
        "grayCandidateHardGatePassed"
    ]
