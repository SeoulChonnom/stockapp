from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pytest

from tests.support import load_module

batch_models_module = load_module('app.batch.models')
steps_module = load_module('app.batch.steps')

BatchExecutionContext = batch_models_module.BatchExecutionContext
BuildClustersStep = steps_module.BuildClustersStep
BuildPageSnapshotStep = steps_module.BuildPageSnapshotStep
CollectMarketIndicesStep = steps_module.CollectMarketIndicesStep
DedupeArticlesStep = steps_module.DedupeArticlesStep
GenerateAiSummariesStep = steps_module.GenerateAiSummariesStep


@dataclass
class EventRecorder:
    session: object
    events: list[tuple[str, str, str]]

    async def add_event(
        self, *, job_id: int, step_code: str, level: str, message: str, **kwargs
    ):
        _ = (job_id, kwargs)
        self.events.append((step_code, level, message))


class EmptyClusterRepo:
    def __init__(self, session):
        _ = session

    async def list_clusters_by_business_date(self, business_date):
        _ = business_date
        return []

    async def list_cluster_article_links_by_business_date(self, business_date):
        _ = business_date
        return []


class EmptyIndexRepo:
    def __init__(self, session):
        _ = session

    async def list_indices_by_business_date(self, business_date):
        _ = business_date
        return []


class EmptySummaryRepo:
    def __init__(self, session):
        _ = session

    async def list_summaries_for_job(self, job_id):
        _ = job_id
        return []


class UnusedSnapshotRepo:
    def __init__(self, session):
        _ = session


class EmptyRawRepo:
    def __init__(self, session):
        _ = session

    async def list_articles_by_business_date(self, business_date, *, market_type=None):
        _ = (business_date, market_type)
        return []


class UnusedProcessedRepo:
    def __init__(self, session):
        _ = session


class EmptyProcessedArticlesRepo:
    def __init__(self, session):
        _ = session

    async def list_by_business_date(self, business_date):
        _ = business_date
        return []


class UnusedClusterWriteRepo:
    def __init__(self, session):
        _ = session


class EmptyMarketIndexProvider:
    async def fetch_for_business_date(self, business_date):
        _ = business_date
        return []


class EmptyLlmProvider:
    def is_configured(self):
        return False


def build_step(step_cls):
    if step_cls is DedupeArticlesStep:
        return step_cls(
            raw_repo_factory=EmptyRawRepo,
            processed_repo_factory=UnusedProcessedRepo,
            content_provider_factory=lambda: object(),
        )
    if step_cls is BuildClustersStep:
        return step_cls(
            processed_repo_factory=EmptyProcessedArticlesRepo,
            cluster_repo_factory=UnusedClusterWriteRepo,
            llm_provider_factory=EmptyLlmProvider,
        )
    if step_cls is CollectMarketIndicesStep:
        return step_cls(
            provider_factory=EmptyMarketIndexProvider,
            index_repo_factory=EmptyIndexRepo,
        )
    if step_cls is GenerateAiSummariesStep:
        return step_cls(
            cluster_repo_factory=EmptyClusterRepo,
            index_repo_factory=EmptyIndexRepo,
            summary_repo_factory=UnusedProcessedRepo,
            llm_provider_factory=EmptyLlmProvider,
        )
    if step_cls is BuildPageSnapshotStep:
        return step_cls(
            cluster_repo_factory=EmptyClusterRepo,
            summary_repo_factory=EmptySummaryRepo,
            index_repo_factory=EmptyIndexRepo,
            snapshot_repo_factory=UnusedSnapshotRepo,
        )
    raise AssertionError(f'Unexpected step class: {step_cls}')


def expected_log_fragment(step_cls):
    if step_cls is DedupeArticlesStep:
        return 'No raw articles were available for deduplication.'
    if step_cls is BuildClustersStep:
        return 'No processed articles were available for clustering.'
    if step_cls is CollectMarketIndicesStep:
        return '시장 지수 데이터를 수집하지 못했습니다.'
    if step_cls is GenerateAiSummariesStep:
        return '요약 생성에 필요한 클러스터가 없습니다.'
    if step_cls is BuildPageSnapshotStep:
        return 'SNAPSHOT_SOURCE_MISSING'
    raise AssertionError(f'Unexpected step class: {step_cls}')


def context_messages(context: BatchExecutionContext) -> list[str]:
    return [
        *context.log_messages,
        *context.partial_reasons,
        *context.warning_messages,
        *( [context.error_code] if context.error_code else [] ),
    ]


@pytest.mark.anyio
@pytest.mark.parametrize(
    'step_cls',
    [
        DedupeArticlesStep,
        BuildClustersStep,
        CollectMarketIndicesStep,
        GenerateAiSummariesStep,
        BuildPageSnapshotStep,
    ],
)
async def test_remaining_batch_steps_emit_lifecycle_events_and_preserve_context(
    step_cls,
):
    repository = EventRecorder(session=object(), events=[])
    context = BatchExecutionContext(
        job_id=1001,
        business_date=date(2026, 3, 17),
        force_run=False,
        rebuild_page_only=False,
    )

    updated_context = await build_step(step_cls).execute(repository, context)

    assert updated_context is context
    assert repository.events[0][0] == step_cls.step_code
    assert repository.events[0][2] in {
        'Dedupe articles step started.',
        'Build clusters step started.',
        'Collect market indices step started.',
        'Generate AI summaries step started.',
        'Build page snapshot step started.',
    }
    assert repository.events[-1][0] == step_cls.step_code
    assert repository.events[-1][2] in {
        'Dedupe articles step completed.',
        'Build clusters step completed.',
        'Collect market indices step completed.',
        'Generate AI summaries step completed.',
        'Build page snapshot step completed.',
    }
    expected = expected_log_fragment(step_cls)
    assert expected in context_messages(context)


@pytest.mark.anyio
@pytest.mark.parametrize(
    'step',
    [
        pytest.param(
            DedupeArticlesStep(
                raw_repo_factory=EmptyRawRepo,
                processed_repo_factory=UnusedProcessedRepo,
                content_provider_factory=lambda: object(),
            ),
            id='dedupe',
        ),
        pytest.param(
            BuildClustersStep(
                processed_repo_factory=EmptyProcessedArticlesRepo,
                cluster_repo_factory=UnusedClusterWriteRepo,
                llm_provider_factory=EmptyLlmProvider,
            ),
            id='build-clusters',
        ),
        pytest.param(
            CollectMarketIndicesStep(
                provider_factory=EmptyMarketIndexProvider,
                index_repo_factory=EmptyIndexRepo,
            ),
            id='collect-market-indices',
        ),
        pytest.param(
            GenerateAiSummariesStep(
                cluster_repo_factory=EmptyClusterRepo,
                index_repo_factory=EmptyIndexRepo,
                summary_repo_factory=UnusedProcessedRepo,
                llm_provider_factory=EmptyLlmProvider,
            ),
            id='generate-ai-summaries',
        ),
        pytest.param(
            BuildPageSnapshotStep(
                cluster_repo_factory=EmptyClusterRepo,
                summary_repo_factory=EmptySummaryRepo,
                index_repo_factory=EmptyIndexRepo,
                snapshot_repo_factory=UnusedSnapshotRepo,
            ),
            id='build-page-snapshot',
        ),
    ],
)
async def test_remaining_batch_steps_require_repository_session(step):
    context = BatchExecutionContext(
        job_id=1001,
        business_date=date(2026, 3, 17),
        force_run=False,
        rebuild_page_only=False,
    )

    with pytest.raises(
        RuntimeError,
        match='requires a repository with an attached session',
    ):
        await step.run(EventRecorder(session=None, events=[]), context)
