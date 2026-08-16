"""The open circuit must degrade every call site, never fail the run.

Suspending doomed calls is only safe if each caller answers the suspension with
fallback content. If any one of them let ``LlmConfigurationError`` escape, its
step would fail and the day's page would never be built -- trading a degraded
page for no page at all, which is the opposite of what the circuit is for.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.batch.providers.llm_provider import BatchLlmProvider
from app.batch.steps.classify_cluster_themes import _classify_target
from app.batch.steps.cluster_enrichment import _enrich_cluster
from app.batch.steps.generate_ai_summaries import (
    _generate_cluster_card_summary,
    _generate_cluster_detail_summary,
    _generate_market_summary,
)
from app.batch.theme_rules import FallbackRule, ThemeRule, ThemeRuleCatalog
from app.core.settings import Settings
from tests.batch.gemini_mock import build_mock_gemini_harness

CLUSTER = {
    'title': '반도체주 조정',
    'summary_short': '반도체주가 하락했습니다.',
    'summary_long': '외국인 매도와 업황 우려가 함께 반영됐습니다.',
    'analysis_paragraphs_json': [],
    'tags_json': [],
}
ARTICLE_ROWS = [
    {
        'id': 1024,
        'canonical_title': '반도체주 약세',
        'source_summary': '외국인 매도가 이어졌습니다.',
        'article_body_excerpt': '반도체 업종이 하락했습니다.',
        'origin_link': 'https://example.com/1024',
    }
]


class _RejectedByConfiguration(Exception):
    """A provider verdict on the request's configuration, as a 404 would be."""

    def __init__(self) -> None:
        self.code = 404
        super().__init__("Error calling model 'x' (404)")


def _enrichment_article() -> SimpleNamespace:
    return SimpleNamespace(
        processed_article_id=1024,
        canonical_title='반도체주 약세',
        publisher_name='테스트뉴스',
        published_at=datetime(2026, 8, 16, tzinfo=UTC),
        source_summary='외국인 매도가 이어졌습니다.',
        article_body_excerpt='반도체 업종이 하락했습니다.',
    )


def _open_circuit_provider(monkeypatch) -> BatchLlmProvider:
    """Build a provider whose circuit trips on its very first rejection."""
    harness = build_mock_gemini_harness(
        monkeypatch,
        [_RejectedByConfiguration()],
        settings=Settings(
            app_env='development',
            gemini_api_key='mock-api-key',
            llm_model='gemini-3.1-flash-lite',
            llm_timeout_seconds=1,
            llm_config_error_circuit_threshold=1,
        ),
    )
    return BatchLlmProvider(harness.client)


@pytest.mark.anyio
async def test_open_circuit_degrades_cluster_enrichment(monkeypatch):
    provider = _open_circuit_provider(monkeypatch)
    articles = [_enrichment_article()]

    first = await _enrich_cluster(provider, 'KR', articles)
    suspended = await _enrich_cluster(provider, 'KR', articles)

    assert first['fallback_used'] is True
    assert suspended['fallback_used'] is True
    assert suspended['error_context']['errorClass'] == 'LlmConfigurationError'
    assert suspended['title']


@pytest.mark.anyio
async def test_open_circuit_degrades_theme_classification(monkeypatch):
    provider = _open_circuit_provider(monkeypatch)
    target = {
        'market_type': 'KR',
        'cluster': {'title': CLUSTER['title']},
        'articles': ARTICLE_ROWS,
    }
    catalog = ThemeRuleCatalog(
        (
            ThemeRule(
                code='semiconductor',
                inclusion_criteria=(),
                exclusion_criteria=(),
                fallback=FallbackRule(
                    enabled=False,
                    minimum_score=6,
                    strong_phrases=(),
                    supporting_term_groups=(),
                    excluded_phrases=(),
                ),
            ),
        )
    )

    await _classify_target(provider, target, catalog)
    suspended = await _classify_target(provider, target, catalog)

    assert suspended['fallback_reason'] == 'provider_error'
    assert suspended['error_context']['errorClass'] == 'LlmConfigurationError'


@pytest.mark.anyio
@pytest.mark.parametrize(
    'generate',
    [
        pytest.param(
            lambda provider: _generate_cluster_card_summary(
                provider, 'KR', CLUSTER, ARTICLE_ROWS
            ),
            id='cluster-card',
        ),
        pytest.param(
            lambda provider: _generate_cluster_detail_summary(
                provider, 'KR', CLUSTER, ARTICLE_ROWS
            ),
            id='cluster-detail',
        ),
        pytest.param(
            lambda provider: _generate_market_summary(
                provider, market_type='KR', clusters=[CLUSTER], indices=[]
            ),
            id='market-summary',
        ),
    ],
)
async def test_open_circuit_degrades_every_summary_call_site(generate, monkeypatch):
    provider = _open_circuit_provider(monkeypatch)

    await generate(provider)
    suspended = await generate(provider)

    assert suspended['status'] == 'FALLBACK'
    assert suspended['fallback_used'] is True
    # The row still carries a cause, so a suspended call stays tellable apart
    # from a model that answered with unusable content.
    assert suspended['metadata_json']['error']['errorClass'] == (
        'LlmConfigurationError'
    )


@pytest.mark.anyio
async def test_the_suspended_call_never_reaches_the_provider(monkeypatch):
    """The point of the circuit is the time saved, not just the tidy failure."""
    harness = build_mock_gemini_harness(
        monkeypatch,
        [_RejectedByConfiguration()],
        settings=Settings(
            app_env='development',
            gemini_api_key='mock-api-key',
            llm_model='gemini-3.1-flash-lite',
            llm_timeout_seconds=1,
            llm_config_error_circuit_threshold=1,
        ),
    )
    provider = BatchLlmProvider(harness.client)
    articles = [_enrichment_article()]

    for _ in range(5):
        await _enrich_cluster(provider, 'KR', articles)

    assert harness.model.call_count == 1
    assert harness.rate_limiter.acquire_count == 1
