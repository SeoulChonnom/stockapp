from __future__ import annotations

import asyncio
from pathlib import Path

from scripts.evaluate_theme_enrichment import (
    DATASET_PATH,
    EXPECTED_CALL_COUNT,
    CallRecord,
    compute_metrics,
    load_dataset,
    run_evaluation,
)


def test_compute_metrics_uses_exact_gate_denominators() -> None:
    records = [
        CallRecord(
            variant='candidate_a',
            cluster_id='KR-01',
            market_type='KR',
            run_index=1,
            http_status=200,
            response_valid=True,
            theme_output_valid=True,
            assigned_codes=['MACRO_ECONOMIC_DATA_INFLATION'],
            fallback_used=False,
            fallback_assignment_count=0,
            primary_correct=True,
            latency_ms=10.0,
            prompt_tokens=10,
            response_tokens=10,
            raw_response_sha256='a' * 64,
        ),
        CallRecord(
            variant='candidate_a',
            cluster_id='KR-01',
            market_type='KR',
            run_index=2,
            http_status=200,
            response_valid=True,
            theme_output_valid=False,
            assigned_codes=['MACRO_ECONOMIC_DATA_INFLATION'],
            fallback_used=True,
            fallback_assignment_count=1,
            primary_correct=True,
            latency_ms=12.0,
            prompt_tokens=12,
            response_tokens=11,
            raw_response_sha256='b' * 64,
        ),
        CallRecord(
            variant='candidate_a',
            cluster_id='KR-01',
            market_type='KR',
            run_index=3,
            http_status=200,
            response_valid=True,
            theme_output_valid=True,
            assigned_codes=['MACRO_ECONOMIC_DATA_INFLATION'],
            fallback_used=False,
            fallback_assignment_count=0,
            primary_correct=True,
            latency_ms=10.0,
            prompt_tokens=10,
            response_tokens=10,
            raw_response_sha256='c' * 64,
        ),
    ]

    metrics = compute_metrics(records)

    assert metrics.call_count == 3
    assert metrics.enrichment_success_rate == 1.0
    assert metrics.invalid_theme_response_rate == 1 / 3
    assert metrics.fallback_assignment_rate == 1.0
    assert metrics.primary_accuracy == 1.0
    assert metrics.three_run_agreement == 1.0
    assert metrics.p95_latency_ms == 12.0
    assert metrics.average_token_usage == 21.0


def test_dataset_is_exactly_balanced_and_provenance_is_explicit() -> None:
    dataset = load_dataset(DATASET_PATH)

    assert len(dataset) == 40
    assert sum(cluster.market_type == 'KR' for cluster in dataset) == 20
    assert sum(cluster.market_type == 'US' for cluster in dataset) == 20
    assert all(cluster.source_provenance for cluster in dataset)
    assert all(cluster.articles for cluster in dataset)
    assert (
        len({article.article_id for cluster in dataset for article in cluster.articles})
        == 80
    )


def test_mockup_evaluation_makes_exactly_240_http_calls() -> None:
    result = asyncio.run(run_evaluation(write_outputs=False))

    assert result.http_call_count == EXPECTED_CALL_COUNT
    assert len(result.records) == EXPECTED_CALL_COUNT
    assert all(record.http_status == 200 for record in result.records)
    assert all(len(record.raw_response_sha256) == 64 for record in result.records)
    assert result.candidate_metrics.invalid_theme_response_rate > 0
    assert result.candidate_metrics.fallback_assignment_rate >= 0.95


def test_evaluation_uses_real_prompt_and_response_sizes() -> None:
    result = asyncio.run(run_evaluation(write_outputs=False))

    baseline_tokens = result.baseline_metrics.average_token_usage
    candidate_tokens = result.candidate_metrics.average_token_usage
    assert candidate_tokens > baseline_tokens
    assert result.candidate_metrics.average_prompt_tokens > (
        result.baseline_metrics.average_prompt_tokens
    )
    assert result.candidate_metrics.average_response_tokens >= (
        result.baseline_metrics.average_response_tokens
    )


def test_result_path_is_stable_under_the_fixture() -> None:
    assert DATASET_PATH == Path('tests/fixtures/theme_enrichment_eval.json')
