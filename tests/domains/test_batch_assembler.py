from copy import deepcopy
from datetime import UTC, datetime
from types import SimpleNamespace

from tests.support import jsonable, load_module

assembler_module = load_module('app.domains.batches.assembler')
projections_module = load_module('app.db.repositories.projections')

BatchJobStepRunRecord = projections_module.BatchJobStepRunRecord


def _sample_market_snapshot_job() -> SimpleNamespace:
    return SimpleNamespace(
        job_id=1001,
        job_name='market_daily_batch',
        business_date=datetime(2026, 3, 17).date(),
        status='SUCCESS',
        run_mode='FULL',
        source_job_id=None,
        source_page_id=None,
        queued_at=datetime(2026, 3, 18, 6, 9, 58, tzinfo=UTC),
        attempt_count=1,
        max_attempts=3,
        current_step='FINALIZE',
        started_at=datetime(2026, 3, 18, 6, 10, 0, tzinfo=UTC),
        ended_at=datetime(2026, 3, 18, 6, 12, 15, tzinfo=UTC),
        duration_seconds=135,
        partial_message=None,
        error_code=None,
        error_message=None,
        log_summary=(
            '정상 처리. 시장 데이터, 기사 수집, 클러스터링이 SLA 안에서 종료됐다.'
        ),
        force_run=False,
        rebuild_page_only=False,
        raw_news_count=174,
        processed_news_count=114,
        cluster_count=21,
        page_id=501,
        page_version_no=3,
        ai_target_count=0,
        ai_attempted_count=0,
        ai_success_count=0,
        ai_fallback_count=0,
        ai_failed_count=0,
        ai_recovered_count=0,
    )


def test_batch_detail_assembler_redacts_legacy_provider_diagnostics(
    sample_batch_job_detail_payload,
) -> None:
    payload = deepcopy(sample_batch_job_detail_payload)
    raw = (
        'AI summary fallback for GLOBAL_HEADLINE: 429 RESOURCE_EXHAUSTED '
        'quota RetryInfo secret-token https://generativelanguage.googleapis.com'
    )
    payload['partialMessage'] = raw
    payload['errorMessage'] = raw
    payload['logSummary'] = raw

    response = jsonable(assembler_module.assemble_batch_job_detail_response(payload))
    serialized = repr(response)

    assert response['partialMessage'] == (
        'AI summary fallback for GLOBAL_HEADLINE: '
        'AI provider request failed; fallback content was used.'
    )
    assert '429' not in serialized
    assert 'RetryInfo' not in serialized
    assert 'secret-token' not in serialized
    assert 'googleapis.com' not in serialized


def test_batch_list_assembler_preserves_normal_partial_reason(
    sample_batch_job_list_payload,
) -> None:
    payload = deepcopy(sample_batch_job_list_payload)
    reason = (
        'Naver news pagination cap was reached before covering the persisted '
        "window for keyword '증시'."
    )
    payload['items'][0]['partialMessage'] = reason

    response = jsonable(assembler_module.assemble_batch_job_list_response(payload))

    assert response['items'][0]['partialMessage'] == reason


def test_detail_payload_includes_step_diagnostics_and_durations():
    job = _sample_market_snapshot_job()
    step_runs = [
        BatchJobStepRunRecord(
            step_run_id=11,
            step_code='CREATE_JOB',
            seq=1,
            status='SUCCEEDED',
            started_at=datetime(2026, 8, 7, 0, 0, tzinfo=UTC),
            ended_at=datetime(2026, 8, 7, 0, 0, 1, tzinfo=UTC),
            duration_ms=1000,
            error_message=None,
            error_log=None,
        ),
        BatchJobStepRunRecord(
            step_run_id=12,
            step_code='COLLECT_NEWS',
            seq=2,
            status='FAILED',
            started_at=datetime(2026, 8, 7, 0, 0, 1, tzinfo=UTC),
            ended_at=datetime(2026, 8, 7, 0, 0, 3, tzinfo=UTC),
            duration_ms=2000,
            error_message='External provider request failed.',
            error_log=(
                'Traceback (most recent call last):\n'
                'Authorization: Bearer injected-provider-token'
            ),
        ),
    ]

    payload = assembler_module.build_batch_job_detail_payload(job, None, step_runs)

    assert [step['stepCode'] for step in payload['steps']] == [
        'CREATE_JOB',
        'COLLECT_NEWS',
    ]
    assert payload['steps'][0]['durationMs'] == 1000
    assert payload['steps'][0]['errorMessage'] is None
    assert payload['steps'][0]['errorLog'] is None
    assert payload['steps'][1]['errorMessage'] == 'External provider request failed.'
    assert 'injected-provider-token' not in payload['steps'][1]['errorLog']
    assert '[REDACTED]' in payload['steps'][1]['errorLog']


def test_detail_payload_defaults_steps_to_empty_list():
    payload = assembler_module.build_batch_job_detail_payload(
        _sample_market_snapshot_job()
    )

    assert payload['steps'] == []
