from __future__ import annotations

import pytest

pytest.importorskip('sqlalchemy')

from tests.support import DummyResult, RecordingAsyncSession, load_module, normalize_sql

ai_summary_repo_module = load_module('app.db.repositories.ai_summary_repo')
settings_module = load_module('app.core.settings')

AiSummaryRepository = ai_summary_repo_module.AiSummaryRepository


@pytest.fixture(autouse=True)
def configure_database_schema(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv('STOCKAPP_DATABASE_SCHEMA', 'stock')
    settings_module.get_settings.cache_clear()
    yield
    settings_module.get_settings.cache_clear()


@pytest.mark.anyio
async def test_list_summaries_for_job_uses_qualified_summary_table():
    session = RecordingAsyncSession(results=[DummyResult([])])
    repository = AiSummaryRepository(session)

    result = await repository.list_summaries_for_job(4)

    assert result == []
    sql = normalize_sql(session.statements[0])
    assert 'from stock.ai_summary' in sql.lower()
    assert 'with recursive' not in sql.lower()
    assert session.parameters[0] == {'job_id': 4}


@pytest.mark.anyio
async def test_list_retry_lineage_summaries_walks_retry_descendants():
    session = RecordingAsyncSession(results=[DummyResult([])])
    repository = AiSummaryRepository(session)

    result = await repository.list_retry_lineage_summaries(4)

    assert result == []
    sql = normalize_sql(session.statements[0]).lower()
    assert 'with recursive lineage_jobs as' in sql
    assert "child.run_mode in ('page_rebuild', 'ai_retry')" in sql
    assert 'cardinality(lineage_jobs.path) < 64' in sql
    assert 'join lineage_jobs' in sql
    assert session.parameters[0] == {'source_job_id': 4}


@pytest.mark.anyio
async def test_get_latest_cluster_summary_uses_qualified_summary_table():
    session = RecordingAsyncSession(results=[DummyResult([])])
    repository = AiSummaryRepository(session)

    result = await repository.get_latest_cluster_summary(
        17,
        summary_type='CLUSTER_CARD_SUMMARY',
    )

    assert result is None
    sql = normalize_sql(session.statements[0])
    assert 'from stock.ai_summary' in sql.lower()
    assert session.parameters[0] == {
        'cluster_id': 17,
        'summary_type': 'CLUSTER_CARD_SUMMARY',
    }
