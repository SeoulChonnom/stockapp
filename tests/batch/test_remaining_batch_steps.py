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
    events: list[tuple[str, str, str]]

    async def add_event(
        self, *, job_id: int, step_code: str, level: str, message: str, **kwargs
    ):
        _ = (job_id, kwargs)
        self.events.append((step_code, level, message))


@pytest.mark.anyio
@pytest.mark.parametrize(
    'step_cls, expected_message',
    [
        (DedupeArticlesStep, 'Article deduplication step is scaffolded.'),
        (BuildClustersStep, 'Cluster building step is scaffolded.'),
        (CollectMarketIndicesStep, 'Market indices collection step is scaffolded.'),
        (GenerateAiSummariesStep, 'AI summary generation step is scaffolded.'),
        (BuildPageSnapshotStep, 'Page snapshot build step is scaffolded.'),
    ],
)
async def test_remaining_batch_steps_emit_lifecycle_events_and_preserve_context(
    step_cls, expected_message
):
    repository = EventRecorder(events=[])
    context = BatchExecutionContext(
        job_id=1001,
        business_date=date(2026, 3, 17),
        force_run=False,
        rebuild_page_only=False,
    )

    updated_context = await step_cls().execute(repository, context)

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
    assert expected_message in context.log_messages
