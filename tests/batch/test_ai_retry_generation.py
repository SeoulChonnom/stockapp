from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from app.batch.ai_retry.models import AiRetrySelection
from app.batch.ai_retry.orchestrator import _generate_target
from app.batch.ai_summary_targets import AiSummaryTarget
from app.db.repositories.projections import AiSummaryRecord


class TransientFailureLlm:
    model_name = 'gemini-test'

    def is_configured(self):
        return True

    async def summarize_global_headline(self, **_kwargs):
        raise TimeoutError('provider timed out')

    async def summarize_market(self, **_kwargs):
        raise RuntimeError('429 RESOURCE_EXHAUSTED')


def _selection(target: AiSummaryTarget) -> AiRetrySelection:
    return AiRetrySelection(
        target=target,
        source_summary=AiSummaryRecord(
            summary_id=1,
            batch_job_id=10,
            summary_type=target.summary_type,
            business_date=date(2026, 7, 28),
            market_type=target.market_type,
            cluster_id=target.cluster_id,
            title='fallback title',
            body='fallback body',
            paragraphs_json=[],
            model_name=None,
            prompt_version='v1',
            status='FALLBACK',
            fallback_used=True,
            error_message=None,
            metadata_json={},
            generated_at=datetime(2026, 7, 29, tzinfo=UTC),
            target_key=target.target_key,
        ),
    )


@pytest.mark.anyio
async def test_timeout_is_persistable_fallback_instead_of_crashing_retry():
    payload = await _generate_target(
        _selection(
            AiSummaryTarget(
                target_key='GLOBAL_HEADLINE',
                summary_type='GLOBAL_HEADLINE',
                market_type=None,
                cluster_id=None,
            )
        ),
        llm_provider=TransientFailureLlm(),
        cluster_repo=object(),
        clusters=[],
        indices=[],
    )

    assert payload['status'] == 'FALLBACK'
    assert payload['fallback_used'] is True
    assert payload['metadata_json']['error']['errorClass'] == 'TimeoutError'


@pytest.mark.anyio
async def test_429_is_persistable_fallback_instead_of_crashing_retry():
    payload = await _generate_target(
        _selection(
            AiSummaryTarget(
                target_key='MARKET_SUMMARY:US',
                summary_type='MARKET_SUMMARY',
                market_type='US',
                cluster_id=None,
            )
        ),
        llm_provider=TransientFailureLlm(),
        cluster_repo=object(),
        clusters=[],
        indices=[],
    )

    assert payload['status'] == 'FALLBACK'
    assert payload['fallback_used'] is True
    assert (
        payload['error_message']
        == 'AI provider request failed; fallback content was used.'
    )
    assert '429' not in repr(payload)
