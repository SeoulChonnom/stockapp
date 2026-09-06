from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from app.batch.models import BatchExecutionContext
from app.batch.policies.market_session_policy import MarketContextDraft
from app.batch.steps.prepare_market_contexts import PrepareMarketContextsStep
from app.db.repositories.projections import BatchJobMarketContextRecord
from tests.support import RecordingAsyncSession


class EventRepository:
    def __init__(self) -> None:
        self.session = RecordingAsyncSession()
        self.events: list[dict] = []

    async def add_event(self, **kwargs) -> None:
        self.events.append(kwargs)


def _context(*, force_run: bool = False) -> BatchExecutionContext:
    return BatchExecutionContext(
        job_id=1001,
        business_date=date(2026, 7, 28),
        force_run=force_run,
        rebuild_page_only=False,
    )


def _record(
    market_type: str,
    *,
    job_id: int = 900,
    start_at: datetime = datetime(2026, 7, 27, 0, 0, tzinfo=UTC),
    end_at: datetime = datetime(2026, 7, 28, 0, 0, tzinfo=UTC),
) -> BatchJobMarketContextRecord:
    return BatchJobMarketContextRecord(
        market_context_id=1,
        batch_job_id=job_id,
        market_type=market_type,
        expected_session_date=date(2026, 7, 27),
        actual_index_source_date=date(2026, 7, 27),
        session_close_at=datetime(2026, 7, 27, 20, 0, tzinfo=UTC),
        news_window_start_at=start_at,
        news_window_end_at=end_at,
        news_coverage_complete=True,
    )


@pytest.mark.anyio
async def test_prepare_market_contexts_reuses_current_job_rows_on_retry():
    class ExistingRepo:
        def __init__(self, _session):
            self.inserts = []

        async def list_for_job(self, job_id):
            return [
                _record('US', job_id=job_id),
                _record('KR', job_id=job_id),
            ]

    repository = ExistingRepo(None)
    step = PrepareMarketContextsStep(
        context_repo_factory=lambda _session: repository,
        policy_factory=lambda: (_ for _ in ()).throw(
            AssertionError('policy must not run on retry')
        ),
    )
    context = _context()

    result = await step.run(EventRepository(), context)

    assert result.log_messages == ['Reused persisted market contexts.']


@pytest.mark.anyio
async def test_prepare_market_contexts_force_run_reuses_original_window():
    original_start = datetime(2026, 7, 27, 1, 15, tzinfo=UTC)
    original_end = datetime(2026, 7, 28, 1, 15, tzinfo=UTC)

    class ForceRepo:
        def __init__(self):
            self.inserts = []

        async def list_for_job(self, _job_id):
            return []

        async def get_latest_for_business_date(
            self, *, business_date, market_type, exclude_job_id
        ):
            _ = (business_date, exclude_job_id)
            return _record(
                market_type,
                start_at=original_start,
                end_at=original_end,
            )

        async def insert_if_absent(self, params):
            self.inserts.append(params)

    context_repo = ForceRepo()
    step = PrepareMarketContextsStep(
        context_repo_factory=lambda _session: context_repo,
        policy_factory=lambda: (_ for _ in ()).throw(
            AssertionError('force run must reuse its original cutoff')
        ),
    )

    await step.run(EventRepository(), _context(force_run=True))

    assert len(context_repo.inserts) == 2
    assert {
        (row.news_window_start_at, row.news_window_end_at)
        for row in context_repo.inserts
    } == {(original_start, original_end)}


@pytest.mark.anyio
async def test_prepare_market_contexts_uses_complete_coverage_watermark():
    cut_off = datetime(2026, 7, 28, 7, 0, tzinfo=UTC)
    watermarks = {
        'US': datetime(2026, 7, 27, 7, 0, tzinfo=UTC),
        'KR': datetime(2026, 7, 27, 8, 0, tzinfo=UTC),
    }

    class ContextRepo:
        def __init__(self):
            self.inserts = []

        async def list_for_job(self, _job_id):
            return []

        async def get_latest_complete_coverage_end(self, *, market_type, at_or_before):
            assert at_or_before == cut_off
            return watermarks[market_type]

        async def insert_if_absent(self, params):
            self.inserts.append(params)

    class RecordingPolicy:
        def __init__(self):
            self.calls = []

        def build_context(self, *, market_type, as_of, previous_coverage_end_at):
            self.calls.append((market_type, as_of, previous_coverage_end_at))
            return MarketContextDraft(
                market_type=market_type,
                expected_session_date=date(2026, 7, 27),
                session_close_at=datetime(2026, 7, 27, 20, 0, tzinfo=UTC),
                news_window_start_at=previous_coverage_end_at,
                news_window_end_at=as_of,
            )

    context_repo = ContextRepo()
    policy = RecordingPolicy()
    step = PrepareMarketContextsStep(
        now_factory=lambda: cut_off,
        context_repo_factory=lambda _session: context_repo,
        policy_factory=lambda: policy,
    )

    await step.run(EventRepository(), _context())

    assert policy.calls == [
        ('US', cut_off, watermarks['US']),
        ('KR', cut_off, watermarks['KR']),
    ]
    assert [row.news_window_start_at for row in context_repo.inserts] == [
        watermarks['US'],
        watermarks['KR'],
    ]


@pytest.mark.anyio
async def test_prepare_market_contexts_warns_but_does_not_degrade_when_capped():
    """A stale watermark is an operator's problem, not the page's.

    The cap only skips spans older than the window, whose articles belong to
    pages already published, and the watermark goes stale as soon as one
    30-minute slot is never marked complete -- every other slot in the
    skipped span having been collected normally. Job 1640 was pushed to
    PARTIAL by exactly that: two capped-window reasons that no reader could
    see, which then crowded the real KR coverage gap out of the bounded
    message. The WARN has to survive; the degradation must not.
    """
    cut_off = datetime(2026, 9, 6, 6, 10, tzinfo=UTC)
    capped_start = datetime(2026, 9, 4, 6, 10, tzinfo=UTC)

    class ContextRepo:
        def __init__(self):
            self.inserts = []

        async def list_for_job(self, _job_id):
            return []

        async def get_latest_complete_coverage_end(self, *, market_type, at_or_before):
            return datetime(2026, 8, 26, 6, 10, tzinfo=UTC)

        async def insert_if_absent(self, params):
            self.inserts.append(params)

    class CappingPolicy:
        def build_context(self, *, market_type, as_of, previous_coverage_end_at):
            assert previous_coverage_end_at is not None
            return MarketContextDraft(
                market_type=market_type,
                expected_session_date=date(2026, 9, 4),
                session_close_at=datetime(2026, 9, 4, 6, 30, tzinfo=UTC),
                news_window_start_at=capped_start,
                news_window_end_at=as_of,
                lookback_capped=True,
            )

    repository = EventRepository()
    step = PrepareMarketContextsStep(
        now_factory=lambda: cut_off,
        context_repo_factory=lambda _session: ContextRepo(),
        policy_factory=CappingPolicy,
    )
    context = _context()

    await step.run(repository, context)

    assert context.partial_categories == {}
    assert context.partial_reasons == []
    assert [event['message'] for event in repository.events] == [
        'News window capped past incomplete coverage.'
    ] * 2
    assert [event['context_json']['marketType'] for event in repository.events] == [
        'US',
        'KR',
    ]


@pytest.mark.anyio
async def test_prepare_market_contexts_stays_clean_when_the_window_was_not_capped():
    """A healthy run must not report a skip it never made."""
    cut_off = datetime(2026, 9, 6, 6, 10, tzinfo=UTC)

    class ContextRepo:
        async def list_for_job(self, _job_id):
            return []

        async def get_latest_complete_coverage_end(self, *, market_type, at_or_before):
            return datetime(2026, 9, 5, 6, 10, tzinfo=UTC)

        async def insert_if_absent(self, params):
            return None

    class HealthyPolicy:
        def build_context(self, *, market_type, as_of, previous_coverage_end_at):
            return MarketContextDraft(
                market_type=market_type,
                expected_session_date=date(2026, 9, 4),
                session_close_at=datetime(2026, 9, 4, 6, 30, tzinfo=UTC),
                news_window_start_at=previous_coverage_end_at,
                news_window_end_at=as_of,
            )

    repository = EventRepository()
    step = PrepareMarketContextsStep(
        now_factory=lambda: cut_off,
        context_repo_factory=lambda _session: ContextRepo(),
        policy_factory=HealthyPolicy,
    )
    context = _context()

    await step.run(repository, context)

    assert context.partial_categories == {}
    assert repository.events == []
