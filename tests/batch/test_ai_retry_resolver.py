from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from app.batch.ai_retry.resolver import (
    calculate_retry_counts,
    resolve_effective_summaries,
    select_retry_targets,
)
from app.db.repositories.projections import AiSummaryRecord

GENERATED_AT = datetime(2026, 7, 29, 1, tzinfo=UTC)


def _summary(
    summary_id: int,
    *,
    job_id: int,
    target_key: str,
    status: str,
    fallback_used: bool,
    attempt_no: int = 1,
    source_summary_id: int | None = None,
) -> AiSummaryRecord:
    summary_type, _, suffix = target_key.partition(':')
    market_type = suffix if summary_type == 'MARKET_SUMMARY' else None
    cluster_id = int(suffix) if summary_type.startswith('CLUSTER_') else None
    return AiSummaryRecord(
        summary_id=summary_id,
        batch_job_id=job_id,
        summary_type=summary_type,
        business_date=date(2026, 7, 28),
        market_type=market_type,
        cluster_id=cluster_id,
        title=f'title-{summary_id}',
        body=f'body-{summary_id}',
        paragraphs_json=[],
        model_name='gemini',
        prompt_version='v1',
        status=status,
        fallback_used=fallback_used,
        error_message=None,
        metadata_json={},
        generated_at=GENERATED_AT + timedelta(seconds=summary_id),
        target_key=target_key,
        source_summary_id=source_summary_id,
        attempt_no=attempt_no,
    )


def test_success_source_is_skipped_and_fallback_only_is_selected():
    source_success = _summary(
        1,
        job_id=10,
        target_key='GLOBAL_HEADLINE',
        status='SUCCESS',
        fallback_used=False,
    )
    source_fallback = _summary(
        2,
        job_id=10,
        target_key='MARKET_SUMMARY:US',
        status='FALLBACK',
        fallback_used=True,
    )

    selected = select_retry_targets(
        source_job_id=10,
        retry_job_id=20,
        lineage=[source_success, source_fallback],
    )

    assert [item.target.target_key for item in selected] == ['MARKET_SUMMARY:US']


def test_success_has_priority_over_a_newer_retry_fallback():
    source = _summary(
        1,
        job_id=10,
        target_key='GLOBAL_HEADLINE',
        status='FALLBACK',
        fallback_used=True,
    )
    recovered = _summary(
        2,
        job_id=20,
        target_key='GLOBAL_HEADLINE',
        status='SUCCESS',
        fallback_used=False,
        source_summary_id=1,
        attempt_no=2,
    )
    later_fallback = _summary(
        3,
        job_id=30,
        target_key='GLOBAL_HEADLINE',
        status='FALLBACK',
        fallback_used=True,
        source_summary_id=2,
        attempt_no=3,
    )

    effective = resolve_effective_summaries([source, recovered, later_fallback])

    assert effective['GLOBAL_HEADLINE'].summary_id == recovered.summary_id


def test_crash_resume_retries_current_fallback_without_self_lineage():
    source = _summary(
        1,
        job_id=10,
        target_key='CLUSTER_CARD_SUMMARY:7',
        status='FALLBACK',
        fallback_used=True,
    )
    current_fallback = _summary(
        2,
        job_id=20,
        target_key='CLUSTER_CARD_SUMMARY:7',
        status='FALLBACK',
        fallback_used=True,
        source_summary_id=1,
        attempt_no=2,
    )

    selected = select_retry_targets(
        source_job_id=10,
        retry_job_id=20,
        lineage=[source, current_fallback],
    )

    assert len(selected) == 1
    assert selected[0].existing_retry is current_fallback
    assert selected[0].source_summary is source


def test_crash_resume_skips_current_success_and_preserves_recovered_count():
    source = _summary(
        1,
        job_id=10,
        target_key='CLUSTER_DETAIL_ANALYSIS:7',
        status='FAILED',
        fallback_used=False,
    )
    current_success = _summary(
        2,
        job_id=20,
        target_key='CLUSTER_DETAIL_ANALYSIS:7',
        status='SUCCESS',
        fallback_used=False,
        source_summary_id=1,
        attempt_no=2,
    )
    lineage = [source, current_success]

    selected = select_retry_targets(
        source_job_id=10,
        retry_job_id=20,
        lineage=lineage,
    )
    counts = calculate_retry_counts(
        source_job_id=10,
        retry_job_id=20,
        lineage=lineage,
    )

    assert selected == []
    assert counts.target_count == 1
    assert counts.attempted_count == 1
    assert counts.success_count == 1
    assert counts.recovered_count == 1


def test_success_status_with_fallback_flag_is_still_retryable():
    inconsistent_source = _summary(
        1,
        job_id=10,
        target_key='GLOBAL_HEADLINE',
        status='SUCCESS',
        fallback_used=True,
    )

    selected = select_retry_targets(
        source_job_id=10,
        retry_job_id=20,
        lineage=[inconsistent_source],
    )

    assert len(selected) == 1
