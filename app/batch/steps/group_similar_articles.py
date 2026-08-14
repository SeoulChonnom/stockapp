from __future__ import annotations

import math
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import datetime
from numbers import Real
from typing import Any, cast

from app.batch.article_similarity import (
    ArticleCandidate,
    SimilarityParameters,
    group_similar_articles,
)
from app.batch.models import BatchExecutionContext
from app.batch.providers.ollama_embedding_provider import (
    EmbeddingArticle,
    OllamaEmbeddingError,
    OllamaEmbeddingProvider,
)
from app.batch.steps.base import BatchStep, require_repository_session
from app.batch.steps.target_progress import DurableTargetProgress
from app.core.public_diagnostics import public_external_provider_error
from app.core.settings import Settings, get_settings
from app.db.enums import EventLevel
from app.db.repositories.batch_job_repo import BatchJobRepository
from app.db.repositories.cluster_repo import ClusterRepository
from app.db.repositories.news_cluster_write_repo import NewsClusterWriteRepository

GROUP_SIMILAR_ARTICLES = 'GROUP_SIMILAR_ARTICLES'
SIMILARITY_THRESHOLD = 0.8
LEXICAL_FEATURE_VERSION = 'lexical-v1'
VETO_VERSION = 'veto-v1'
GROUPING_VERSION = 'complete-link-v1'

_EXPECTED_CLUSTER_ERRORS = (OllamaEmbeddingError, ValueError, TypeError)


def build_grouping_algorithm_version(
    *,
    model: str,
    input_chars: int,
    parameters: SimilarityParameters | None = None,
    threshold: float = SIMILARITY_THRESHOLD,
) -> str:
    """Build a deterministic, secret-free version for persisted grouping rows."""
    if not isinstance(model, str) or not model.strip():
        raise ValueError('embedding model must be a non-empty string')
    if isinstance(input_chars, bool) or not isinstance(input_chars, int):
        raise ValueError('similarity input cap must be an integer')
    if input_chars <= 0:
        raise ValueError('similarity input cap must be positive')
    if isinstance(threshold, bool) or not isinstance(threshold, Real):
        raise ValueError('similarity threshold must be numeric')
    numeric_threshold = float(threshold)
    if not math.isfinite(numeric_threshold) or not 0.0 <= numeric_threshold <= 1.0:
        raise ValueError('similarity threshold must be between 0 and 1')
    selected = parameters or SimilarityParameters()
    weights = ','.join(
        f'{value:.6g}'
        for value in (
            selected.title_weight,
            selected.full_text_weight,
            selected.numeric_date_weight,
            selected.ticker_name_org_weight,
            selected.dense_weight,
            selected.lexical_weight,
        )
    )
    return (
        f'model={model.strip()};inputChars={input_chars};'
        f'lexical={LEXICAL_FEATURE_VERSION};weights={weights};'
        f'threshold={numeric_threshold:.6g};veto={VETO_VERSION};'
        f'grouping={GROUPING_VERSION}'
    )


class GroupSimilarArticlesStep(BatchStep):
    """Group persisted cluster articles one cluster at a time."""

    step_code = GROUP_SIMILAR_ARTICLES
    started_message = 'Group similar articles step started.'
    completed_message = 'Group similar articles step completed.'

    def __init__(
        self,
        *,
        cluster_repo_factory: Callable[[object], Any] | None = None,
        group_repo_factory: Callable[[object], Any] | None = None,
        cluster_write_repo_factory: Callable[[object], Any] | None = None,
        embedding_provider_factory: Callable[[], Any] | None = None,
        provider_factory: Callable[[], Any] | None = None,
        settings: Settings | Any | None = None,
        parameters: SimilarityParameters | None = None,
        threshold: float | None = None,
    ) -> None:
        resolved_settings = settings or get_settings()
        self._cluster_repo_factory = cluster_repo_factory or ClusterRepository
        self._group_repo_factory = group_repo_factory
        self._cluster_write_repo_factory = cluster_write_repo_factory
        provider = embedding_provider_factory or provider_factory
        if provider is None:

            def default_provider() -> Any:
                return OllamaEmbeddingProvider(resolved_settings)

            provider = default_provider
        self._embedding_provider_factory = provider
        self._model = _required_text(
            getattr(resolved_settings, 'ollama_embed_model', 'bge-m3'),
            field='ollama_embed_model',
        )
        self._input_chars = _positive_int(
            getattr(resolved_settings, 'similarity_input_chars', 2048),
            field='similarity_input_chars',
        )
        self._threshold = (
            threshold
            if threshold is not None
            else getattr(
                resolved_settings, 'similarity_threshold', SIMILARITY_THRESHOLD
            )
        )
        self._parameters = parameters or SimilarityParameters()
        self._algorithm_version = build_grouping_algorithm_version(
            model=self._model,
            input_chars=self._input_chars,
            parameters=self._parameters,
            threshold=self._threshold,
        )

    async def run(
        self,
        repository: BatchJobRepository,
        context: BatchExecutionContext,
    ) -> BatchExecutionContext:
        if context.rebuild_page_only:
            context.log_messages.append(
                'Skipped similar article grouping because rebuild_page_only=true.'
            )
            return context

        session = require_repository_session(repository, step_code=self.step_code)
        cluster_repo = self._cluster_repo_factory(session)
        group_repo = self._resolve_group_repository(session, cluster_repo)
        embedding_provider = self._embedding_provider_factory()
        clusters = await cluster_repo.list_clusters_by_business_date(
            context.business_date
        )
        ordered_clusters = _ordered_clusters(clusters)
        if not ordered_clusters:
            context.log_messages.append(
                'No persisted clusters were available for similar article grouping.'
            )
            return context

        progress = await DurableTargetProgress.load(
            repository,
            job_id=context.job_id,
            step_code=self.step_code,
        )
        for cluster in ordered_clusters:
            cluster_id = _as_int(_row_value(cluster, 'id', 'cluster_id'))
            target = await self._load_target(cluster_repo, cluster_id, cluster)
            target_key = f'cluster:{cluster_id}:grouping:{self._algorithm_version}'
            if await _is_current_grouping(
                cluster_repo,
                cluster_id,
                target['article_ids'],
                self._algorithm_version,
            ):
                continue

            try:
                vectors = await _embed_articles(
                    embedding_provider, target['embedding_articles']
                )
                if len(vectors) != len(target['articles']):
                    raise ValueError('embedding count does not match article count')
                candidates = [
                    ArticleCandidate(
                        processed_article_id=article_id,
                        canonical_title=_text_value(
                            article, 'canonical_title', 'title'
                        ),
                        source_summary=_text_value(
                            article, 'source_summary', 'summary'
                        ),
                        article_body_excerpt=_text_value(
                            article, 'article_body_excerpt', 'body_excerpt', 'body'
                        ),
                        publisher_name=_text_value(
                            article, 'publisher_name', 'publisher'
                        ),
                        origin_link=_text_value(article, 'origin_link', 'originLink'),
                        published_at=_datetime_value(
                            article, 'published_at', 'publishedAt'
                        ),
                        vector=tuple(cast(Sequence[Any], vectors[index])),
                        exact_duplicate_count=target['exact_counts'][article_id],
                        is_cluster_representative=article_id
                        == target['representative_article_id'],
                    )
                    for index, (article_id, article) in enumerate(
                        zip(target['article_ids'], target['articles'], strict=True)
                    )
                ]
                result = group_similar_articles(
                    candidates,
                    threshold=self._threshold,
                    parameters=self._parameters,
                )
            except _EXPECTED_CLUSTER_ERRORS as exc:
                await repository.add_event(
                    job_id=context.job_id,
                    step_code=self.step_code,
                    level=EventLevel.WARN.value,
                    message='Similar article grouping used unavailable fallback.',
                    context_json={
                        'clusterId': cluster_id,
                        'errorCode': 'SIMILARITY_GROUPING_FAILED',
                        'error': public_external_provider_error(type(exc).__name__),
                    },
                )
                await group_repo.mark_grouping_unavailable_with_singletons(
                    cluster_id,
                    [
                        {'processed_article_id': article_id}
                        for article_id in target['article_ids']
                    ],
                    target['exact_counts'],
                    self._algorithm_version,
                )
            else:
                await group_repo.replace_cluster_groups(
                    cluster_id,
                    result,
                    algorithm_version=self._algorithm_version,
                    exact_counts=target['exact_counts'],
                )
            await progress.commit_target(target_key, context)

        context.log_messages.append(
            f'Grouped similar articles for {len(ordered_clusters)} cluster(s).'
        )
        return context

    def _resolve_group_repository(self, session: Any, cluster_repo: Any) -> Any:
        if self._group_repo_factory is not None:
            return self._group_repo_factory(session)
        if hasattr(cluster_repo, 'replace_cluster_groups'):
            return cluster_repo
        factory = self._cluster_write_repo_factory or NewsClusterWriteRepository
        return factory(session)

    async def _load_target(
        self, cluster_repo: Any, cluster_id: int, cluster: object
    ) -> dict[str, Any]:
        memberships = await cluster_repo.get_cluster_articles(cluster_id)
        article_ids = [
            _as_int(_row_value(row, 'processed_article_id')) for row in memberships
        ]
        if len(article_ids) != len(set(article_ids)):
            raise ValueError('cluster article IDs must be unique')
        exact_count_rows = await cluster_repo.get_exact_duplicate_counts(article_ids)
        exact_counts: dict[int, int] = {}
        for row in exact_count_rows:
            article_id = _as_int(_row_value(row, 'processed_article_id'))
            if article_id in exact_counts:
                raise ValueError('exact duplicate counts must use unique IDs')
            exact_counts[article_id] = _nonnegative_int(
                _row_value(row, 'exact_duplicate_count')
            )
        if set(exact_counts) != set(article_ids):
            raise ValueError('exact duplicate counts must cover cluster articles')
        raw_articles = await cluster_repo.get_processed_articles(article_ids)
        by_id = {
            _as_int(_row_value(row, 'id', 'processed_article_id')): row
            for row in raw_articles
        }
        if set(by_id) != set(article_ids):
            raise ValueError('processed article evidence must cover cluster articles')
        articles = [by_id[article_id] for article_id in article_ids]
        embedding_articles = [
            EmbeddingArticle(
                canonical_title=_text_value(article, 'canonical_title', 'title'),
                source_summary=_text_value(article, 'source_summary', 'summary'),
                article_body_excerpt=_text_value(
                    article, 'article_body_excerpt', 'body_excerpt', 'body'
                ),
            )
            for article in articles
        ]
        return {
            'article_ids': article_ids,
            'articles': articles,
            'embedding_articles': embedding_articles,
            'exact_counts': exact_counts,
            'representative_article_id': _row_value(
                cluster, 'representative_article_id', 'representativeArticleId'
            ),
        }


async def _embed_articles(provider: Any, articles: Sequence[EmbeddingArticle]) -> list:
    embed = getattr(provider, 'embed_articles', None)
    if not callable(embed):
        embed = getattr(provider, 'embed', None)
    if not callable(embed):
        raise TypeError('embedding provider does not expose an embed method')
    invoke = cast(Callable[[Sequence[EmbeddingArticle]], Awaitable[object]], embed)
    vectors = await invoke(articles)
    if isinstance(vectors, (str, bytes)) or not isinstance(vectors, Sequence):
        raise ValueError('embedding provider returned an invalid collection')
    return list(cast(Sequence[Any], vectors))


async def _is_current_grouping(
    repository: Any,
    cluster_id: int,
    article_ids: Sequence[int],
    algorithm_version: str,
) -> bool:
    getter = getattr(repository, 'get_cluster_grouping', None)
    if not callable(getter):
        return False
    invoke = cast(Callable[[int], Awaitable[object]], getter)
    grouping = await invoke(cluster_id)
    if (
        grouping is None
        or getattr(grouping, 'algorithm_version', None) != algorithm_version
    ):
        return False
    members = getattr(grouping, 'members', ())
    member_ids = [_row_value(member, 'processed_article_id') for member in members]
    return len(member_ids) == len(article_ids) and set(member_ids) == set(article_ids)


def _ordered_clusters(
    clusters: Sequence[Mapping[str, object]],
) -> list[Mapping[str, object]]:
    return sorted(
        clusters,
        key=lambda cluster: (
            str(_row_value(cluster, 'market_type') or ''),
            _as_int(_row_value(cluster, 'cluster_rank')),
            _as_int(_row_value(cluster, 'id', 'cluster_id')),
        ),
    )


def _row_value(row: object, *names: str) -> object:
    for name in names:
        if isinstance(row, Mapping) and name in row:
            return row[name]
        value = getattr(row, name, None)
        if value is not None:
            return value
    return None


def _text_value(row: object, *names: str) -> str | None:
    value = _row_value(row, *names)
    return value if isinstance(value, str) else None


def _datetime_value(row: object, *names: str) -> datetime | None:
    value = _row_value(row, *names)
    if value is None or isinstance(value, datetime):
        return value
    raise TypeError('published_at must be a datetime or None')


def _as_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f'expected integer value, got {value!r}')
    return value


def _nonnegative_int(value: object) -> int:
    integer = _as_int(value)
    if integer < 0:
        raise ValueError('expected nonnegative integer')
    return integer


def _positive_int(value: object, *, field: str) -> int:
    integer = _as_int(value)
    if integer <= 0:
        raise ValueError(f'{field} must be positive')
    return integer


def _required_text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{field} must be a non-empty string')
    return value.strip()


__all__ = [
    'GROUP_SIMILAR_ARTICLES',
    'GROUPING_VERSION',
    'GroupSimilarArticlesStep',
    'LEXICAL_FEATURE_VERSION',
    'SIMILARITY_THRESHOLD',
    'VETO_VERSION',
    'build_grouping_algorithm_version',
]
