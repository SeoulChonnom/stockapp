from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Mapping, Sequence
from functools import partial
from typing import Any

from app.batch.logging import log_safe_exception
from app.batch.models import BatchExecutionContext
from app.batch.providers.llm_provider import (
    THEME_CLASSIFIER_PROMPT_VERSION,
    BatchLlmProvider,
)
from app.batch.steps.base import BatchStep, require_repository_session
from app.batch.steps.target_progress import (
    DurableTargetProgress,
    TargetCall,
    run_target_calls,
)
from app.batch.theme_classifier import (
    ArticleEvidence,
    ThemeAssignment,
    ThemeEvidence,
    classify_theme_fallback,
)
from app.batch.theme_contract import (
    THEME_CLASSIFICATION,
    THEME_CLASSIFICATION_MISSING,
)
from app.batch.theme_rules import ThemeRuleCatalog, load_theme_rules
from app.core.llm import LlmRetryableError
from app.core.public_diagnostics import public_ai_provider_error
from app.db.enums import EventLevel
from app.db.repositories.batch_job_repo import BatchJobRepository
from app.db.repositories.cluster_repo import ClusterRepository
from app.db.repositories.news_cluster_write_repo import NewsClusterWriteRepository
from app.db.repositories.projections import ThemeAssignmentCreateParams
from app.db.repositories.theme_repo import ThemeRepository

LOGGER = logging.getLogger(__name__)

CLASSIFY_CLUSTER_THEMES = 'CLASSIFY_CLUSTER_THEMES'


def _parse_theme_codes(
    value: object,
    *,
    allowed_theme_codes: Sequence[str],
) -> list[str]:
    """Keep valid provider codes in order, deduplicated and capped at three."""

    if not isinstance(value, list):
        return []
    allowed = set(allowed_theme_codes)
    parsed: list[str] = []
    for candidate in value:
        if not isinstance(candidate, str) or candidate not in allowed:
            continue
        if candidate in parsed:
            continue
        parsed.append(candidate)
        if len(parsed) == 3:
            break
    return parsed


class ClassifyClusterThemesStep(BatchStep):
    step_code = CLASSIFY_CLUSTER_THEMES
    started_message = 'Classify cluster themes step started.'
    completed_message = 'Classify cluster themes step completed.'

    def __init__(
        self,
        *,
        cluster_repo_factory: Callable[[object], Any] | None = None,
        cluster_write_repo_factory: Callable[[object], Any] | None = None,
        theme_repository_factory: Callable[[object], Any] | None = None,
        llm_provider_factory: Callable[[], Any] | None = None,
    ) -> None:
        self._cluster_repo_factory = cluster_repo_factory or ClusterRepository
        self._cluster_write_repo_factory = cluster_write_repo_factory
        self._theme_repository_factory = theme_repository_factory or ThemeRepository
        self._llm_provider_factory = llm_provider_factory or BatchLlmProvider

    async def run(
        self,
        repository: BatchJobRepository,
        context: BatchExecutionContext,
    ) -> BatchExecutionContext:
        if context.rebuild_page_only:
            context.log_messages.append(
                'Skipped cluster theme classification because rebuild_page_only=true.'
            )
            return context

        session = require_repository_session(repository, step_code=self.step_code)
        theme_catalog = await _load_theme_catalog(
            self._theme_repository_factory(session)
        )
        cluster_repo = self._cluster_repo_factory(session)
        if self._cluster_write_repo_factory is None and hasattr(
            cluster_repo, 'replace_cluster_themes'
        ):
            cluster_write_repo = cluster_repo
        else:
            write_factory = (
                self._cluster_write_repo_factory or NewsClusterWriteRepository
            )
            cluster_write_repo = write_factory(session)
        llm_provider = self._llm_provider_factory()
        clusters = await cluster_repo.list_clusters_by_business_date(
            context.business_date
        )
        if not clusters:
            context.log_messages.append(
                'No persisted clusters were available for theme classification.'
            )
            return context

        targets = await _build_classification_targets(
            cluster_repo=cluster_repo,
            clusters=clusters,
            theme_catalog=theme_catalog,
        )
        progress = await DurableTargetProgress.load(
            repository,
            job_id=context.job_id,
            step_code=self.step_code,
        )
        concurrency_limit = getattr(llm_provider, 'concurrency_limit', 1)
        semaphore = asyncio.Semaphore(concurrency_limit)

        async def bounded_classify(target: dict[str, Any]) -> dict[str, Any]:
            async with semaphore:
                return await _classify_target(llm_provider, target, theme_catalog)

        targets_by_key = {target['target_key']: target for target in targets}
        persist_result = partial(
            _persist_classification,
            context=context,
            repository=repository,
            step_code=self.step_code,
            cluster_write_repo=cluster_write_repo,
            targets_by_key=targets_by_key,
            progress=progress,
        )
        pending_calls = [
            TargetCall(
                target_key=target['target_key'],
                invoke=partial(bounded_classify, target),
            )
            for target in targets
            if target['target_key'] not in progress.completed_targets
        ]
        await run_target_calls(pending_calls, on_result=persist_result)
        context.log_messages.append(f'Classified themes for {len(targets)} cluster(s).')
        return context


async def _load_theme_catalog(theme_repository: Any) -> ThemeRuleCatalog:
    """Validate active database leaves against the Git-managed rule catalog."""

    rows = await theme_repository.list_active_tree_rows()
    active_rows = [row for row in rows if _row_value(row, 'is_active') is True]
    parent_codes = {
        parent_code
        for row in active_rows
        if isinstance(parent_code := _row_value(row, 'parent_code'), str)
    }
    leaf_codes = [
        code
        for row in active_rows
        if isinstance(code := _row_value(row, 'code'), str) and code not in parent_codes
    ]
    return load_theme_rules(tuple(leaf_codes))


async def _build_classification_targets(
    *,
    cluster_repo: Any,
    clusters: Sequence[Mapping[str, Any]],
    theme_catalog: ThemeRuleCatalog,
) -> list[dict[str, Any]]:
    """Read persisted cluster evidence in deterministic repository order."""

    ordered_clusters = sorted(
        clusters,
        key=lambda cluster: (
            str(cluster.get('market_type', '')),
            _as_int(cluster.get('cluster_rank')),
            _as_int(cluster.get('id', cluster.get('cluster_id'))),
        ),
    )
    targets: list[dict[str, Any]] = []
    for cluster in ordered_clusters:
        cluster_id = _as_int(cluster.get('id', cluster.get('cluster_id')))
        memberships = await cluster_repo.get_cluster_articles(cluster_id)
        article_ids = [
            _as_int(_row_value(membership, 'processed_article_id'))
            for membership in memberships
        ]
        processed_rows = await cluster_repo.get_processed_articles(article_ids)
        processed_by_id = {
            _as_int(_row_value(article, 'id', 'processed_article_id')): article
            for article in processed_rows
        }
        missing_ids = [
            article_id
            for article_id in article_ids
            if article_id not in processed_by_id
        ]
        if missing_ids:
            raise ValueError(
                f'cluster {cluster_id} is missing processed article evidence: '
                f'{missing_ids}'
            )
        articles = [processed_by_id[article_id] for article_id in article_ids]
        evidence = _build_theme_evidence(cluster, articles, article_ids)
        payload_articles = [
            _article_prompt_payload(article_id, article)
            for article_id, article in zip(article_ids, articles, strict=True)
        ]
        target_key = build_theme_classification_target_key(cluster_id)
        targets.append(
            {
                'target_key': target_key,
                'cluster_id': cluster_id,
                'market_type': str(cluster.get('market_type', '')),
                'cluster': {
                    'title': _row_value(cluster, 'title', 'cluster_title'),
                },
                'articles': payload_articles,
                'evidence': evidence,
                'allowed_theme_codes': theme_catalog.codes,
                'theme_catalog': theme_catalog,
            }
        )
    return targets


async def _classify_target(
    llm_provider: Any,
    target: dict[str, Any],
    theme_catalog: ThemeRuleCatalog,
) -> dict[str, Any]:
    if not llm_provider.is_configured():
        return {
            'response': {},
            'fallback_reason': 'provider_unconfigured',
            'error_context': None,
        }
    try:
        response = await llm_provider.classify_cluster_themes(
            market_type=target['market_type'],
            cluster=target['cluster'],
            articles=target['articles'],
            theme_codes=theme_catalog.codes,
        )
    except LlmRetryableError:
        raise
    except Exception as exc:
        log_safe_exception(
            LOGGER,
            logging.WARNING,
            'Cluster theme classifier request failed.',
            exception=exc,
            context={'model': getattr(llm_provider, 'model_name', None)},
        )
        return {
            'response': {},
            'fallback_reason': 'provider_error',
            'error_context': public_ai_provider_error(exc),
        }
    return {
        'response': response,
        'fallback_reason': None,
        'error_context': None,
    }


async def _persist_classification(
    target_key: str,
    payload: dict[str, Any],
    *,
    context: BatchExecutionContext,
    repository: BatchJobRepository,
    step_code: str,
    cluster_write_repo: Any,
    targets_by_key: dict[str, dict[str, Any]],
    progress: DurableTargetProgress,
) -> None:
    target = targets_by_key[target_key]
    response = payload.get('response')
    valid_codes = _parse_theme_codes(
        response.get('themeCodes') if isinstance(response, Mapping) else None,
        allowed_theme_codes=target['allowed_theme_codes'],
    )
    if valid_codes:
        assignments = [
            ThemeAssignment(
                theme_code=code,
                rank=rank,
                classification_method='LLM',
            )
            for rank, code in enumerate(valid_codes, start=1)
        ]
        fallback_used = False
    else:
        assignments = classify_theme_fallback(
            target['evidence'],
            target['theme_catalog'],
        )
        fallback_used = True

    persistence_assignments = _to_theme_assignment_create_params(assignments)
    await cluster_write_repo.replace_cluster_themes(
        target['cluster_id'],
        persistence_assignments,
    )
    if fallback_used:
        context.fallback_count += 1
        await repository.add_event(
            job_id=context.job_id,
            step_code=step_code,
            level=EventLevel.WARN.value,
            message='Cluster theme classification used keyword fallback.',
            context_json={
                'clusterId': target['cluster_id'],
                'targetKey': target_key,
                'fallbackReason': payload.get('fallback_reason'),
                'error': payload.get('error_context'),
            },
        )
    if fallback_used and not persistence_assignments:
        context.add_partial(
            THEME_CLASSIFICATION,
            THEME_CLASSIFICATION_MISSING,
        )
        await repository.add_event(
            job_id=context.job_id,
            step_code=step_code,
            level=EventLevel.WARN.value,
            message='Cluster theme classification produced no assignment.',
            context_json={
                'clusterId': target['cluster_id'],
                'targetKey': target_key,
                'reason': THEME_CLASSIFICATION_MISSING,
            },
        )
    await progress.commit_target(target_key, context)


def _build_theme_evidence(
    cluster: Mapping[str, Any],
    articles: Sequence[Mapping[str, Any]],
    article_ids: Sequence[int],
) -> ThemeEvidence:
    representative_article_id = _row_value(cluster, 'representative_article_id')
    representative_id = (
        _as_int(representative_article_id)
        if representative_article_id is not None
        else (article_ids[0] if article_ids else None)
    )
    return ThemeEvidence(
        cluster_title=_as_optional_text(_row_value(cluster, 'title', 'cluster_title')),
        representative_article_id=representative_id,
        articles=tuple(
            ArticleEvidence(
                article_id=article_id,
                title=_as_optional_text(
                    _row_value(article, 'canonical_title', 'title')
                ),
                source_summary=_as_optional_text(
                    _row_value(article, 'source_summary', 'summary')
                ),
                article_body_excerpt=_as_optional_text(
                    _row_value(article, 'article_body_excerpt', 'excerpt')
                ),
            )
            for article_id, article in zip(article_ids, articles, strict=True)
        ),
    )


def _article_prompt_payload(
    article_id: int, article: Mapping[str, Any]
) -> dict[str, Any]:
    return {
        'processedArticleId': article_id,
        'title': _row_value(article, 'canonical_title', 'title'),
        'publisherName': _row_value(article, 'publisher_name', 'publisherName'),
        'publishedAt': _row_value(article, 'published_at', 'publishedAt'),
        'summary': _row_value(article, 'source_summary', 'summary'),
        'excerpt': _row_value(article, 'article_body_excerpt', 'excerpt'),
    }


def _to_theme_assignment_create_params(
    assignments: Sequence[ThemeAssignment],
) -> list[ThemeAssignmentCreateParams]:
    return [
        ThemeAssignmentCreateParams(
            theme_code=assignment.theme_code,
            rank=assignment.rank,
            classification_method=assignment.classification_method,
        )
        for assignment in assignments
    ]


def build_theme_classification_target_key(cluster_id: int) -> str:
    return f'cluster:{cluster_id}:theme-classifier:{THEME_CLASSIFIER_PROMPT_VERSION}'


def _row_value(row: object, *field_names: str) -> object:
    for field_name in field_names:
        if isinstance(row, Mapping) and field_name in row:
            return row[field_name]
        value = getattr(row, field_name, None)
        if value is not None:
            return value
    return None


def _as_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f'expected integer identifier, got {value!r}')
    return value


def _as_optional_text(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f'expected text evidence, got {type(value).__name__}')
    return value


__all__ = [
    'CLASSIFY_CLUSTER_THEMES',
    'ClassifyClusterThemesStep',
    'THEME_CLASSIFICATION',
    'THEME_CLASSIFICATION_MISSING',
    'THEME_CLASSIFIER_PROMPT_VERSION',
    '_parse_theme_codes',
    'build_theme_classification_target_key',
]
