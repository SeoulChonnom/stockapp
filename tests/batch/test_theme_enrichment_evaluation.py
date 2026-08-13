from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

from scripts.evaluate_theme_enrichment import (
    BASELINE_SYSTEM_PROMPT,
    BASELINE_SYSTEM_PROMPT_SHA256,
    DATASET_PATH,
    EXPECTED_CALL_COUNT,
    CallRecord,
    _audit_theme_output,
    _content_contract_valid,
    compute_metrics,
    evaluate_gates,
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
            theme_zero_valid=True,
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


def test_historical_baseline_prompt_is_frozen() -> None:
    """Pin the exact pre-Task5 production prompt from parent 36411a6."""

    assert BASELINE_SYSTEM_PROMPT == (
        'You are a financial news clustering assistant. '
        'Return a single JSON object with keys: title, summary_short, '
        'summary_long, tags, representative_article_index, '
        'analysis_paragraphs.'
    )
    prompt_bytes = BASELINE_SYSTEM_PROMPT.encode('utf-8')
    assert len(prompt_bytes) == 178
    assert hashlib.sha256(prompt_bytes).hexdigest() == BASELINE_SYSTEM_PROMPT_SHA256


def test_theme_audit_uses_production_subset_parser() -> None:
    allowed = ('MACRO_ECONOMIC_DATA_INFLATION', 'SECTOR_AUTOS_MOBILITY')

    raw_invalid, reason, zero_valid, accepted = _audit_theme_output(
        {'themeCodes': ['MACRO_ECONOMIC_DATA_INFLATION', 'UNKNOWN_LEAF']}, allowed
    )
    assert raw_invalid is True
    assert reason == 'UNKNOWN_CODE'
    assert zero_valid is False
    assert accepted == ['MACRO_ECONOMIC_DATA_INFLATION']

    raw_invalid, reason, zero_valid, accepted = _audit_theme_output(
        {'themeCodes': ['MACRO_ECONOMIC_DATA_INFLATION'] * 2}, allowed
    )
    assert raw_invalid is True
    assert reason == 'DUPLICATE_CODE'
    assert zero_valid is False
    assert accepted == ['MACRO_ECONOMIC_DATA_INFLATION']


def test_content_contract_matches_production_parser_semantics() -> None:
    # Candidate enrichment omits evaluator-only representative_article_index;
    # production defaults it to article zero and only validates optional shapes.
    assert _content_contract_valid(
        {'title': 'event', 'tags': [], 'analysis_paragraphs': []}, 2
    )
    # Production normalizes an out-of-range representative index to zero.
    assert _content_contract_valid(
        {
            'title': 'event',
            'representative_article_index': 999,
            'tags': [],
            'analysis_paragraphs': [],
        },
        2,
    )
    assert not _content_contract_valid({'tags': 'not-a-list'}, 2)


def test_written_hash_manifest_matches_artifact_bytes(tmp_path: Path) -> None:
    result_path = tmp_path / 'theme-eval.json'
    report_path = tmp_path / 'theme-eval.md'
    result = asyncio.run(
        run_evaluation(
            write_outputs=True,
            result_path=result_path,
            report_path=report_path,
            repeatability_runs=0,
        )
    )

    manifest = json.loads(
        (tmp_path / 'theme-eval.manifest.json').read_text(encoding='utf-8')
    )
    assert (
        manifest['resultJsonSha256']
        == hashlib.sha256(result_path.read_bytes()).hexdigest()
    )
    assert (
        manifest['reportMarkdownSha256']
        == hashlib.sha256(report_path.read_bytes()).hexdigest()
    )
    assert (
        result.provenance['baselinePrompt']['sha256'] == BASELINE_SYSTEM_PROMPT_SHA256
    )
    assert (
        result.provenance['evaluatorScriptSha256']
        == hashlib.sha256(
            Path('scripts/evaluate_theme_enrichment.py').read_bytes()
        ).hexdigest()
    )


def test_latency_gate_can_be_marked_non_decisive_after_repeatability_crossing() -> None:
    result = asyncio.run(run_evaluation(write_outputs=False, repeatability_runs=0))
    checks = evaluate_gates(
        result.baseline_metrics,
        result.candidate_metrics,
        latency_status='NON_DECISIVE',
    )
    assert checks['p95_latency_increase']['status'] == 'NON_DECISIVE'
