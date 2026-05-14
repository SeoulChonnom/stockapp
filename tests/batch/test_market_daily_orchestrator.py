from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timezone

import pytest

from tests.support import DummyResult, RecordingAsyncSession, load_module

orchestrator_module = load_module('app.batch.orchestrators.market_daily')
projections_module = load_module('app.db.repositories.projections')

MarketDailyBatchOrchestrator = orchestrator_module.MarketDailyBatchOrchestrator
BatchJobRecord = projections_module.BatchJobRecord


class FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        _ = (exc_type, exc, tb)
        return False


class FakeSessionMaker:
    def __call__(self):
        return FakeSession()


@dataclass
class FakeRepository:
    events: list[tuple[str, str]]
    completed_statuses: list[str]
    session: RecordingAsyncSession

    async def get_job_by_id(self, job_id: int):
        return BatchJobRecord(
            job_id=job_id,
            job_name='market_daily_batch',
            business_date=date(2026, 3, 17),
            status='RUNNING',
            started_at=datetime(2026, 3, 18, 6, 10, tzinfo=UTC),
            ended_at=None,
            duration_seconds=None,
            market_scope='GLOBAL',
            raw_news_count=0,
            processed_news_count=0,
            cluster_count=0,
            page_id=None,
            page_version_no=None,
            force_run=False,
            rebuild_page_only=False,
        )

    async def add_event(self, *, step_code: str, message: str, **kwargs):
        _ = kwargs
        self.events.append((step_code, message))

    async def mark_job_completed(self, *, status: str, **kwargs):
        _ = kwargs
        self.completed_statuses.append(status)

    async def mark_job_failed(self, *, error_code: str, error_message: str):
        self.events.append(('FAILED', f'{error_code}:{error_message}'))


@pytest.mark.anyio
async def test_market_daily_orchestrator_runs_all_scaffold_steps(monkeypatch):
    fake_repository = FakeRepository(
        events=[],
        completed_statuses=[],
        session=RecordingAsyncSession(results=[DummyResult([])]),
    )

    monkeypatch.setattr(
        orchestrator_module,
        'BatchJobRepository',
        lambda session: fake_repository,
    )
    orchestrator = MarketDailyBatchOrchestrator(session_maker=FakeSessionMaker())

    await orchestrator.run(1001)

    step_codes = [step_code for step_code, _message in fake_repository.events]
    assert step_codes[0] == 'ORCHESTRATE'
    assert 'COLLECT_NEWS' in step_codes
    assert 'DEDUPE_ARTICLES' in step_codes
    assert 'BUILD_CLUSTERS' in step_codes
    assert 'COLLECT_MARKET_INDICES' in step_codes
    assert 'GENERATE_AI_SUMMARIES' in step_codes
    assert 'BUILD_PAGE_SNAPSHOT' in step_codes
    assert 'FINALIZE_JOB' in step_codes
    assert fake_repository.completed_statuses == ['SUCCESS']
