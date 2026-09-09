from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.batch.diagnostics import (
    INDEX_FETCH_FAILED,
    INDEX_FUTURE_SOURCE_DATE,
    INDEX_STALE_SOURCE_DATE,
)
from app.batch.models import BatchExecutionContext
from app.batch.providers.market_index_provider import (
    EXPECTED_SESSION_CLOSE_NOT_FINITE,
    MarketIndexFetchResult,
    MarketIndexSessionFallback,
)
from app.batch.steps.collect_market_indices import CollectMarketIndicesStep
from app.db.repositories.projections import BatchJobMarketContextRecord
from tests.support import RecordingAsyncSession


class EventRepository:
    def __init__(self) -> None:
        self.session = RecordingAsyncSession()
        self.events = []

    async def add_event(self, **kwargs):
        self.events.append(kwargs)


class ContextRepository:
    def __init__(self, expected_session_date: date) -> None:
        self.expected_session_date = expected_session_date
        self.source_updates = []

    async def list_for_job(self, job_id):
        return [
            BatchJobMarketContextRecord(
                market_context_id=1,
                batch_job_id=job_id,
                market_type='US',
                expected_session_date=self.expected_session_date,
                actual_index_source_date=None,
                session_close_at=datetime(2026, 7, 27, 20, 0, tzinfo=UTC),
                news_window_start_at=datetime(2026, 7, 27, 0, 0, tzinfo=UTC),
                news_window_end_at=datetime(2026, 7, 28, 0, 0, tzinfo=UTC),
                news_coverage_complete=True,
            )
        ]

    async def set_actual_index_source_date(self, **kwargs):
        self.source_updates.append(kwargs)


class IndexRepository:
    def __init__(self) -> None:
        self.rows = []

    async def upsert_index(self, params):
        self.rows.append(params)


def _context() -> BatchExecutionContext:
    return BatchExecutionContext(
        job_id=1001,
        business_date=date(2026, 7, 28),
        force_run=False,
        rebuild_page_only=False,
    )


def _result(source_date: date) -> MarketIndexFetchResult:
    return MarketIndexFetchResult(
        market_type='US',
        index_code='^GSPC',
        index_name='S&P 500',
        currency_code='USD',
        source_date=source_date,
        close_price=Decimal('100.0000'),
        change_value=Decimal('1.0000'),
        change_percent=Decimal('1.0000'),
        high_price=Decimal('101.0000'),
        low_price=Decimal('99.0000'),
    )


@pytest.mark.anyio
async def test_matching_index_source_is_info_and_not_partial():
    expected = date(2026, 7, 27)

    class Provider:
        last_failures = []

        async def fetch_for_business_date(
            self, _business_date, *, expected_session_dates
        ):
            assert expected_session_dates == {'US': expected}
            return [_result(expected)]

    context_repo = ContextRepository(expected)
    index_repo = IndexRepository()
    repository = EventRepository()

    result = await CollectMarketIndicesStep(
        provider_factory=Provider,
        index_repo_factory=lambda _session: index_repo,
        context_repo_factory=lambda _session: context_repo,
    ).run(repository, _context())

    assert result.partial_reasons == []
    assert result.partial_categories == {}
    assert len(index_repo.rows) == 1
    assert index_repo.rows[0].source_date == expected
    assert context_repo.source_updates[0]['source_date'] == expected
    assert any(
        event['level'] == 'INFO'
        and event['message'] == 'Market index matched the expected completed session.'
        for event in repository.events
    )


@pytest.mark.anyio
async def test_stale_index_source_is_persisted_and_marks_partial():
    expected = date(2026, 7, 27)
    stale = date(2026, 7, 24)

    class Provider:
        last_failures = []

        async def fetch_for_business_date(self, *_args, **_kwargs):
            return [_result(stale)]

    context_repo = ContextRepository(expected)
    index_repo = IndexRepository()

    result = await CollectMarketIndicesStep(
        provider_factory=Provider,
        index_repo_factory=lambda _session: index_repo,
        context_repo_factory=lambda _session: context_repo,
    ).run(EventRepository(), _context())

    assert len(index_repo.rows) == 1
    assert any('stale source date' in reason for reason in result.partial_reasons)
    assert result.partial_categories == {INDEX_STALE_SOURCE_DATE: 1}


def _fallback(
    *, used_source_date: date, recovered_from_quote: bool
) -> MarketIndexSessionFallback:
    return MarketIndexSessionFallback(
        provider='YFINANCE',
        market_type='US',
        index_code='^GSPC',
        index_name='S&P 500',
        expected_session_date=date(2026, 7, 27),
        used_source_date=used_source_date,
        reason_code=EXPECTED_SESSION_CLOSE_NOT_FINITE,
        recovered_from_quote=recovered_from_quote,
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    ('recovered', 'used_source_date', 'expected_message'),
    [
        (
            True,
            date(2026, 7, 27),
            'Recovered a market index close from the provider quote.',
        ),
        (
            False,
            date(2026, 7, 24),
            'Fell back to an earlier market index session.',
        ),
    ],
    ids=['recovered_from_quote', 'fell_back_a_session'],
)
async def test_unreadable_expected_session_is_recorded_either_way(
    recovered, used_source_date, expected_message
):
    """An unreadable daily bar must leave a trace even when it was recovered.

    Before this, the provider dropped the expected session's close without
    a word and only the resulting source_date hinted at it, which is why it
    ran unnoticed for twenty-eight days. The event carries the reason code
    so the next occurrence is diagnosable from the job alone.
    """

    class Provider:
        last_failures = []
        last_session_fallbacks = [
            _fallback(used_source_date=used_source_date, recovered_from_quote=recovered)
        ]

        async def fetch_for_business_date(self, *_args, **_kwargs):
            return [_result(used_source_date)]

    repository = EventRepository()

    await CollectMarketIndicesStep(
        provider_factory=Provider,
        index_repo_factory=lambda _session: IndexRepository(),
        context_repo_factory=lambda _session: ContextRepository(date(2026, 7, 27)),
    ).run(repository, _context())

    recorded = [
        event for event in repository.events if event['message'] == expected_message
    ]
    assert len(recorded) == 1
    assert recorded[0]['level'] == 'WARN'
    assert recorded[0]['context_json'] == {
        'provider': 'YFINANCE',
        'marketType': 'US',
        'indexCode': '^GSPC',
        'expectedSessionDate': '2026-07-27',
        'usedSourceDate': used_source_date.isoformat(),
        'reasonCode': EXPECTED_SESSION_CLOSE_NOT_FINITE,
        'recoveredFromQuote': recovered,
    }


@pytest.mark.anyio
async def test_a_recovered_session_is_not_reported_as_degraded():
    """Recovering the expected session's close is not a page defect.

    The reading is the session the page asked for, so nothing about the
    published numbers is stale -- only the provider's daily bars were, and
    that is what the WARN event above is for.
    """

    class Provider:
        last_failures = []
        last_session_fallbacks = [
            _fallback(used_source_date=date(2026, 7, 27), recovered_from_quote=True)
        ]

        async def fetch_for_business_date(self, *_args, **_kwargs):
            return [_result(date(2026, 7, 27))]

    result = await CollectMarketIndicesStep(
        provider_factory=Provider,
        index_repo_factory=lambda _session: IndexRepository(),
        context_repo_factory=lambda _session: ContextRepository(date(2026, 7, 27)),
    ).run(EventRepository(), _context())

    assert result.partial_categories == {}
    assert result.partial_reasons == []


@pytest.mark.anyio
async def test_future_index_source_is_rejected_and_marks_partial():
    expected = date(2026, 7, 27)

    class Provider:
        last_failures = []

        async def fetch_for_business_date(self, *_args, **_kwargs):
            return [_result(date(2026, 7, 28))]

    context_repo = ContextRepository(expected)
    index_repo = IndexRepository()

    result = await CollectMarketIndicesStep(
        provider_factory=Provider,
        index_repo_factory=lambda _session: index_repo,
        context_repo_factory=lambda _session: context_repo,
    ).run(EventRepository(), _context())

    assert index_repo.rows == []
    assert context_repo.source_updates == []
    assert any('future source date' in reason for reason in result.partial_reasons)
    assert result.partial_categories[INDEX_FUTURE_SOURCE_DATE] == 1


@pytest.mark.anyio
async def test_missing_ticker_marks_partial():
    expected = date(2026, 7, 27)

    class Provider:
        last_failures = [
            SimpleNamespace(
                provider='YFINANCE',
                market_type='US',
                ticker='^GSPC',
                index_code='^GSPC',
                index_name='S&P 500',
                error_class='MissingMarketIndexData',
                error_message='no row',
            )
        ]

        async def fetch_for_business_date(self, *_args, **_kwargs):
            return []

    result = await CollectMarketIndicesStep(
        provider_factory=Provider,
        index_repo_factory=lambda _session: IndexRepository(),
        context_repo_factory=lambda _session: ContextRepository(expected),
    ).run(EventRepository(), _context())

    assert any(
        'Market index collection failed for ^GSPC' in reason
        for reason in result.partial_reasons
    )
    assert result.partial_categories[INDEX_FETCH_FAILED] == 1
