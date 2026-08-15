from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pytest

from app.batch.diagnostics import (
    AI_SUMMARY_FALLBACK,
    INDEX_STALE_SOURCE_DATE,
    NEWS_COLLECT_FAILED,
    PARTIAL_UNCATEGORIZED,
    build_attempt_log_line,
    build_diagnostic_log_line,
    build_log_summary,
)
from app.batch.models import BatchExecutionContext
from app.batch.steps.finalize_job import FinalizeJobStep


def build_context() -> BatchExecutionContext:
    return BatchExecutionContext(
        job_id=1001,
        business_date=date(2026, 3, 17),
        force_run=False,
        rebuild_page_only=False,
    )


@dataclass
class FinalizeRepository:
    session: object
    events: list[dict]
    completed: dict | None = None

    async def add_event(self, *, step_code: str, message: str, **kwargs) -> None:
        self.events.append({'step_code': step_code, 'message': message, **kwargs})

    async def mark_job_completed(self, **kwargs) -> None:
        self.completed = kwargs


def test_add_partial_counts_each_distinct_reason_once() -> None:
    context = build_context()

    context.add_partial(INDEX_STALE_SOURCE_DATE, 'KR:^KQ11 used stale source date.')
    context.add_partial(INDEX_STALE_SOURCE_DATE, 'KR:^KS11 used stale source date.')
    context.add_partial(INDEX_STALE_SOURCE_DATE, 'KR:^KS11 used stale source date.')

    assert context.partial_categories == {INDEX_STALE_SOURCE_DATE: 2}
    assert context.partial_reasons == [
        'KR:^KQ11 used stale source date.',
        'KR:^KS11 used stale source date.',
    ]


def test_add_partial_with_paired_warning_counts_the_category_once() -> None:
    context = build_context()

    context.add_partial(
        NEWS_COLLECT_FAILED,
        "Naver news collection failed for keyword 'KOSPI'.",
        warning='Failed to collect Naver news for keyword: KOSPI',
    )

    assert context.partial_categories == {NEWS_COLLECT_FAILED: 1}
    assert context.warning_messages == [
        'Failed to collect Naver news for keyword: KOSPI'
    ]


def test_partial_categories_survive_checkpoint_round_trip() -> None:
    context = build_context()
    context.add_partial(AI_SUMMARY_FALLBACK, 'AI summary fallback for KR cluster 1.')

    restored = BatchExecutionContext.from_checkpoint(
        context.to_checkpoint(),
        job_id=context.job_id,
        business_date=context.business_date,
        force_run=context.force_run,
        rebuild_page_only=context.rebuild_page_only,
    )

    assert restored.partial_categories == {AI_SUMMARY_FALLBACK: 1}


def test_partial_categories_default_to_empty_for_legacy_checkpoints() -> None:
    context = build_context()
    payload = context.to_checkpoint()
    del payload['partialCategories']

    restored = BatchExecutionContext.from_checkpoint(
        payload,
        job_id=context.job_id,
        business_date=context.business_date,
        force_run=context.force_run,
        rebuild_page_only=context.rebuild_page_only,
    )

    assert restored.partial_categories == {}


def test_diagnostic_log_line_orders_by_count_then_category() -> None:
    context = build_context()
    context.partial_categories = {
        INDEX_STALE_SOURCE_DATE: 5,
        AI_SUMMARY_FALLBACK: 24,
        NEWS_COLLECT_FAILED: 5,
    }

    assert build_diagnostic_log_line(context) == (
        'PARTIAL diagnostics: AI_SUMMARY_FALLBACK x24, INDEX_STALE_SOURCE_DATE x5, '
        'NEWS_COLLECT_FAILED x5.'
    )


def test_diagnostic_log_line_is_absent_without_diagnostics() -> None:
    assert build_diagnostic_log_line(build_context()) is None


def test_diagnostic_log_line_reports_uncategorized_fallback_only_partials() -> None:
    context = build_context()
    context.fallback_count = 3

    assert build_diagnostic_log_line(context) == (
        f'PARTIAL diagnostics: {PARTIAL_UNCATEGORIZED} x3.'
    )


def test_no_batch_step_appends_a_partial_signal_without_a_category() -> None:
    """Guard the invariant the whole feature rests on.

    A direct append bypasses ``add_partial``, so the reason would reach
    ``partial_message`` while contributing nothing to the log summary -- exactly
    the silent PARTIAL this feature exists to prevent.
    """
    step_sources = Path('app/batch/steps').glob('*.py')
    offenders = [
        source.name
        for source in step_sources
        if 'partial_reasons.append(' in source.read_text()
        or 'warning_messages.append(' in source.read_text()
    ]

    assert offenders == []


@pytest.mark.anyio
async def test_finalize_job_appends_diagnostic_line_to_log_summary() -> None:
    repository = FinalizeRepository(session=object(), events=[])
    context = build_context()
    context.page_id = 501
    context.log_messages.append('Collected 5 market index row(s).')
    context.add_partial(INDEX_STALE_SOURCE_DATE, 'KR:^KS11 used stale source date.')

    await FinalizeJobStep().run(repository, context)

    assert repository.completed is not None
    assert repository.completed['status'] == 'PARTIAL'
    assert repository.completed['log_summary'] == (
        'Collected 5 market index row(s). '
        f'PARTIAL diagnostics: {INDEX_STALE_SOURCE_DATE} x1.'
    )


@pytest.mark.anyio
async def test_finalize_job_leaves_log_summary_clean_on_success() -> None:
    repository = FinalizeRepository(session=object(), events=[])
    context = build_context()
    context.page_id = 501
    context.log_messages.append('Collected 5 market index row(s).')

    await FinalizeJobStep().run(repository, context)

    assert repository.completed is not None
    assert repository.completed['status'] == 'SUCCESS'
    assert repository.completed['log_summary'] == 'Collected 5 market index row(s).'


def test_attempt_log_line_is_absent_on_a_first_attempt() -> None:
    assert build_attempt_log_line(build_context()) is None


def test_attempt_log_line_reports_a_restart_without_llm_retries() -> None:
    context = build_context()
    context.attempt_count = 2

    assert build_attempt_log_line(context) == 'Job ran on attempt 2.'


def test_attempt_log_line_reports_transient_llm_retries() -> None:
    """Job 507 burned two attempts on Gemini 503s before completing.

    Nothing in the PARTIAL categories recorded that, and a job can restart and
    still finish SUCCESS, so the restart needs its own line in the summary.
    """
    context = build_context()
    context.attempt_count = 3
    context.llm_retry_count = 2

    assert build_attempt_log_line(context) == (
        'Job ran on attempt 3 after 2 transient LLM retry(s).'
    )


def test_log_summary_reports_a_restart_even_without_degradation() -> None:
    context = build_context()
    context.attempt_count = 2
    context.llm_retry_count = 1
    context.log_messages = ['Built page snapshot pageId=4, versionNo=1.']

    assert build_log_summary(context) == (
        'Built page snapshot pageId=4, versionNo=1. '
        'Job ran on attempt 2 after 1 transient LLM retry(s).'
    )


def test_log_summary_keeps_the_restart_out_of_public_reasons() -> None:
    """A retry is operational, not something wrong with the day's content.

    partial_reasons is rendered as public page issues, so the restart must
    reach the log summary without ever entering that list.
    """
    context = build_context()
    context.attempt_count = 3
    context.llm_retry_count = 2
    context.partial_categories = {AI_SUMMARY_FALLBACK: 2}
    context.partial_reasons = ['AI summary fallback for CLUSTER_CARD_SUMMARY/KR: x']

    summary = build_log_summary(context)

    assert summary is not None
    assert 'Job ran on attempt 3 after 2 transient LLM retry(s).' in summary
    assert 'PARTIAL diagnostics: AI_SUMMARY_FALLBACK x2.' in summary
    assert context.partial_reasons == [
        'AI summary fallback for CLUSTER_CARD_SUMMARY/KR: x'
    ]
