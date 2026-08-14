from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Mapping
from copy import deepcopy
from typing import Any

from app.batch.ai_output_contracts import KEY_POINT_FAILURE
from app.batch.models import BatchExecutionContext
from app.batch.normalizers import metadata_optional_string, metadata_string_list
from app.batch.snapshot_contract import require_snapshot_cluster_id
from app.batch.steps.base import BatchStep, require_repository_session
from app.batch.steps.build_page_snapshot_rebuild import (
    rebuild_page_snapshot_from_persisted_page,
)
from app.batch.theme_classifier import normalize_text
from app.batch.theme_contract import (
    THEME_CLASSIFICATION,
    THEME_CLASSIFICATION_MISSING,
    THEME_CLASSIFICATION_MISSING_MESSAGE,
    theme_classification_missing_issue,
)
from app.core.public_diagnostics import (
    sanitize_public_diagnostic,
    sanitize_public_diagnostics,
)
from app.core.text import normalize_search_document
from app.db.enums import AiSummaryType, EventLevel, MarketType, PageStatus
from app.db.repositories.ai_summary_repo import AiSummaryRepository
from app.db.repositories.batch_job_repo import BatchJobRepository
from app.db.repositories.cluster_repo import ClusterRepository
from app.db.repositories.market_context_repo import MarketContextRepository
from app.db.repositories.market_index_repo import MarketIndexRepository
from app.db.repositories.page_snapshot_repo import PageSnapshotRepository
from app.db.repositories.page_snapshot_write_repo import PageSnapshotWriteRepository

SUPPORTED_MARKET_TYPES = (MarketType.US, MarketType.KR)
MARKET_LABELS = {
    MarketType.US: '미국 증시 일간 요약',
    MarketType.KR: '한국 증시 일간 요약',
}


def _build_search_document(
    *values: str | list[str] | tuple[str, ...] | None,
) -> str:
    """Build one normalized, space-delimited snapshot search document."""
    normalized_values: list[str] = []
    for value in values:
        candidates = [value] if isinstance(value, str) else value
        if candidates is None:
            continue
        for candidate in candidates:
            if not isinstance(candidate, str):
                continue
            normalized = normalize_text(candidate)
            if normalized:
                normalized_values.append(normalized)
    return ' '.join(normalized_values)


def _row_value(row: Mapping[str, Any] | object, key: str) -> Any:
    if isinstance(row, Mapping):
        return row.get(key)
    return getattr(row, key, None)


def _group_article_links_by_cluster(
    article_links: list[dict[str, Any]],
) -> tuple[dict[int, list[dict[str, Any]]], dict[str, list[dict[str, Any]]]]:
    by_id: dict[int, list[dict[str, Any]]] = defaultdict(list)
    by_uid: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for article_link in article_links:
        cluster_id = article_link.get('cluster_id')
        if cluster_id is not None:
            by_id[cluster_id].append(article_link)
        cluster_uid = article_link.get('cluster_uid')
        if cluster_uid is not None:
            by_uid[str(cluster_uid)].append(article_link)
    return by_id, by_uid


def _article_links_for_cluster(
    cluster: Mapping[str, Any],
    *,
    by_id: Mapping[int, list[dict[str, Any]]],
    by_uid: Mapping[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    cluster_id = cluster.get('id')
    if cluster_id is not None and cluster_id in by_id:
        return by_id[cluster_id]
    cluster_uid = cluster.get('cluster_uid')
    if cluster_uid is not None:
        return by_uid.get(str(cluster_uid), [])
    return []


def _representative_article_link(
    cluster: Mapping[str, Any], article_links: list[dict[str, Any]]
) -> dict[str, Any] | None:
    representative_id = cluster.get('representative_article_id')
    return next(
        (
            article_link
            for article_link in article_links
            if article_link.get('processed_article_id') == representative_id
        ),
        None,
    )


def _market_news_count(
    counts_by_market: dict[str, int],
    market_type: str,
    fallback: int,
) -> int:
    """Look up a per-market news count, falling back to the job-wide total.

    The fallback only applies when no per-market counts were recorded at
    all (e.g. a job resumed from a checkpoint saved before per-market
    counts existed) -- it never re-mixes the job-wide total into an
    otherwise-populated per-market breakdown.
    """
    if counts_by_market:
        return counts_by_market.get(market_type, 0)
    return fallback


def _structured_page_issues(
    context: BatchExecutionContext,
) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    for reason in sanitize_public_diagnostics(context.partial_reasons):
        if reason == KEY_POINT_FAILURE['message']:
            issues.append(dict(KEY_POINT_FAILURE))
            continue
        theme_issue = theme_classification_missing_issue(reason)
        if theme_issue is not None:
            if theme_issue not in issues:
                issues.append(theme_issue)
            continue
        is_ai_issue = reason.startswith('AI summary fallback')
        issues.append(
            {
                'category': 'AI_SUMMARY' if is_ai_issue else 'BATCH_PARTIAL',
                'code': 'AI_SUMMARY_FALLBACK' if is_ai_issue else 'BATCH_PARTIAL',
                'message': reason,
            }
        )
    issues.extend(
        {
            'category': 'BATCH_WARNING',
            'code': 'BATCH_WARNING',
            'message': warning,
        }
        for warning in sanitize_public_diagnostics(context.warning_messages)
    )
    return issues


def _normalize_theme_partial_diagnostics(
    context: BatchExecutionContext,
) -> None:
    """Collapse classifier aliases before public page metadata is written."""
    normalized_reasons: list[str] = []
    for reason in context.partial_reasons:
        issue = theme_classification_missing_issue(reason)
        normalized_reason = issue['message'] if issue is not None else reason
        if normalized_reason not in normalized_reasons:
            normalized_reasons.append(normalized_reason)
    context.partial_reasons[:] = normalized_reasons

    if context.partial_message:
        partial_message = context.partial_message
        for alias in (
            THEME_CLASSIFICATION_MISSING,
            THEME_CLASSIFICATION_MISSING_MESSAGE,
        ):
            partial_message = partial_message.replace(
                alias, THEME_CLASSIFICATION_MISSING_MESSAGE
            )
        context.partial_message = partial_message


def _global_key_point_metadata(
    global_headline_summary: object | None,
) -> tuple[list[dict[str, str]], dict[str, str] | None]:
    metadata = getattr(global_headline_summary, 'metadata_json', None)
    if not isinstance(metadata, Mapping):
        return [], None

    raw_key_points = metadata.get('keyPoints')
    key_points = deepcopy(raw_key_points) if isinstance(raw_key_points, list) else []
    raw_issue = metadata.get('keyPointIssue')
    if (
        isinstance(raw_issue, Mapping)
        and raw_issue.get('code') == KEY_POINT_FAILURE['code']
    ):
        return key_points, dict(KEY_POINT_FAILURE)
    return key_points, None


def _validate_snapshot_public_identities(
    clusters: list[dict[str, Any]],
    article_links: list[dict[str, Any]],
) -> None:
    for cluster in clusters:
        if cluster.get('cluster_uid') is None:
            raise ValueError('cluster_uid must not be null in a current snapshot')
    for article_link in article_links:
        if article_link.get('processed_article_id') is None:
            raise ValueError(
                'processed_article_id must not be null in a current snapshot'
            )
        if article_link.get('cluster_uid') is None:
            raise ValueError('cluster_uid must not be null in a current snapshot')


class BuildPageSnapshotStep(BatchStep):
    step_code = 'BUILD_PAGE_SNAPSHOT'
    started_message = 'Build page snapshot step started.'
    completed_message = 'Build page snapshot step completed.'

    def __init__(
        self,
        *,
        cluster_repo_factory: Callable[[object], Any] | None = None,
        summary_repo_factory: Callable[[object], Any] | None = None,
        index_repo_factory: Callable[[object], Any] | None = None,
        source_page_repo_factory: Callable[[object], Any] | None = None,
        snapshot_repo_factory: Callable[[object], Any] | None = None,
        context_repo_factory: Callable[[object], Any] | None = None,
    ) -> None:
        self._cluster_repo_factory = cluster_repo_factory or ClusterRepository
        self._summary_repo_factory = summary_repo_factory or AiSummaryRepository
        self._index_repo_factory = index_repo_factory or MarketIndexRepository
        self._source_page_repo_factory = (
            source_page_repo_factory or PageSnapshotRepository
        )
        self._snapshot_repo_factory = (
            snapshot_repo_factory or PageSnapshotWriteRepository
        )
        self._context_repo_factory = context_repo_factory or MarketContextRepository

    async def run(
        self,
        repository: BatchJobRepository,
        context: BatchExecutionContext,
    ) -> BatchExecutionContext:
        session = require_repository_session(repository, step_code=self.step_code)

        snapshot_repo = self._snapshot_repo_factory(session)
        if context.rebuild_page_only:
            return await rebuild_page_snapshot_from_persisted_page(
                repository=repository,
                context=context,
                source_page_repo=self._source_page_repo_factory(session),
                snapshot_repo=snapshot_repo,
                step_code=self.step_code,
            )

        cluster_repo = self._cluster_repo_factory(session)
        summary_repo = self._summary_repo_factory(session)
        index_repo = self._index_repo_factory(session)

        clusters = await cluster_repo.list_clusters_by_business_date(
            context.business_date
        )
        cluster_article_links = (
            await cluster_repo.list_cluster_article_links_by_business_date(
                context.business_date
            )
        )
        indices = await index_repo.list_indices_by_business_date(context.business_date)
        summaries = await summary_repo.list_summaries_for_job(context.job_id)
        if not clusters:
            context.error_code = 'SNAPSHOT_SOURCE_MISSING'
            context.error_message = '스냅샷 생성에 필요한 클러스터 데이터가 없습니다.'
            await repository.add_event(
                job_id=context.job_id,
                step_code=self.step_code,
                level=EventLevel.WARN.value,
                message='Skipped page snapshot creation because no clusters exist.',
                context_json={'businessDate': context.business_date.isoformat()},
            )
            return context
        _validate_snapshot_public_identities(clusters, cluster_article_links)
        theme_rows = await cluster_repo.list_cluster_themes_by_business_date(
            context.business_date
        )
        themes_by_cluster_id: dict[int, list[object]] = defaultdict(list)
        for theme in theme_rows:
            cluster_id = _row_value(theme, 'cluster_id')
            if cluster_id is not None:
                themes_by_cluster_id[int(cluster_id)].append(
                    {
                        'theme_code': _row_value(theme, 'theme_code'),
                        'rank': _row_value(theme, 'rank'),
                    }
                )
        missing_theme_cluster_ids = [
            cluster['id']
            for cluster in clusters
            if not themes_by_cluster_id.get(cluster['id'])
        ]
        theme_issue_already_recorded = any(
            theme_classification_missing_issue(reason) is not None
            for reason in context.partial_reasons
        )
        for _cluster_id in missing_theme_cluster_ids:
            if theme_issue_already_recorded:
                continue
            context.add_partial(
                THEME_CLASSIFICATION,
                THEME_CLASSIFICATION_MISSING_MESSAGE,
            )
            theme_issue_already_recorded = True
        market_contexts = {
            row.market_type: row
            for row in await self._context_repo_factory(session).list_for_job(
                context.job_id
            )
        }
        missing_market_contexts = [
            market_type
            for market_type in SUPPORTED_MARKET_TYPES
            if market_type not in market_contexts
        ]
        if missing_market_contexts:
            context.error_code = 'MARKET_CONTEXT_MISSING'
            context.error_message = (
                '스냅샷 생성에 필요한 시장 세션 컨텍스트가 없습니다.'
            )
            await repository.add_event(
                job_id=context.job_id,
                step_code=self.step_code,
                level=EventLevel.WARN.value,
                message='Skipped page snapshot creation because market contexts are missing.',
                context_json={'marketTypes': missing_market_contexts},
            )
            return context

        summary_by_type: dict[tuple[str, str | None, int | None], object] = {}
        for summary in summaries:
            summary_by_type[
                (summary.summary_type, summary.market_type, summary.cluster_id)
            ] = summary
        global_headline_summary = summary_by_type.get(
            (AiSummaryType.GLOBAL_HEADLINE.value, None, None)
        )
        key_points, key_point_issue = _global_key_point_metadata(
            global_headline_summary
        )
        if key_point_issue is not None:
            context.add_partial(
                KEY_POINT_FAILURE['code'],
                KEY_POINT_FAILURE['message'],
            )

        _normalize_theme_partial_diagnostics(context)
        if not context.partial_message:
            partial_messages = sanitize_public_diagnostics(
                [*context.partial_reasons, *context.warning_messages]
            )
            if partial_messages:
                context.partial_message = '; '.join(partial_messages[:3])
        else:
            context.partial_message = sanitize_public_diagnostic(
                context.partial_message
            )

        version_no = await snapshot_repo.get_next_version_no(context.business_date)
        page_status = (
            PageStatus.PARTIAL.value
            if (
                context.partial_message
                or context.partial_reasons
                or context.warning_messages
                or context.fallback_count
            )
            else PageStatus.READY.value
        )
        page_title = f'글로벌 시장 일간 요약 - {context.business_date.isoformat()}'
        global_headline = getattr(global_headline_summary, 'title', None)
        page_id = await snapshot_repo.create_page(
            business_date=context.business_date,
            version_no=version_no,
            page_title=page_title,
            status=page_status,
            global_headline=global_headline,
            search_document=normalize_search_document(page_title, global_headline),
            partial_message=context.partial_message,
            raw_news_count=context.raw_news_count,
            processed_news_count=context.processed_news_count,
            cluster_count=context.cluster_count,
            batch_job_id=context.job_id,
            metadata_json={
                'warnings': sanitize_public_diagnostics(context.warning_messages),
                'issues': _structured_page_issues(context),
                'keyPoints': key_points,
            },
        )
        by_market: dict[str, list[dict]] = {
            market_type: [] for market_type in SUPPORTED_MARKET_TYPES
        }
        for cluster in clusters:
            by_market.setdefault(cluster['market_type'], []).append(cluster)
        article_links_by_market: dict[str, list[dict]] = {
            market_type: [] for market_type in SUPPORTED_MARKET_TYPES
        }
        for article_link in cluster_article_links:
            article_links_by_market.setdefault(article_link['market_type'], []).append(
                article_link
            )
        article_links_by_cluster_id, article_links_by_cluster_uid = (
            _group_article_links_by_cluster(cluster_article_links)
        )
        indices_by_market: dict[str, list] = {
            market_type: [] for market_type in SUPPORTED_MARKET_TYPES
        }
        for index in indices:
            indices_by_market.setdefault(index.market_type, []).append(index)

        for display_order, market_type in enumerate(SUPPORTED_MARKET_TYPES, start=1):
            market_context = market_contexts[market_type]
            market_summary = summary_by_type.get(
                (AiSummaryType.MARKET_SUMMARY.value, market_type, None)
            )
            market_metadata = getattr(market_summary, 'metadata_json', {}) or {}
            market_background = metadata_string_list(market_metadata, 'background')
            market_key_themes = metadata_string_list(market_metadata, 'keyThemes')
            market_outlook = metadata_optional_string(market_metadata, 'outlook')
            page_market_id = await snapshot_repo.create_page_market(
                page_id=page_id,
                market_type=market_type,
                expected_session_date=market_context.expected_session_date,
                actual_index_source_date=market_context.actual_index_source_date,
                session_close_at=market_context.session_close_at,
                news_window_start_at=market_context.news_window_start_at,
                news_window_end_at=market_context.news_window_end_at,
                news_coverage_complete=market_context.news_coverage_complete,
                display_order=display_order,
                market_label=MARKET_LABELS[market_type],
                summary_title=getattr(market_summary, 'title', None),
                summary_body=getattr(market_summary, 'body', None),
                analysis_background_json=market_background,
                analysis_key_themes_json=market_key_themes,
                analysis_outlook=market_outlook,
                search_document=_build_search_document(
                    MARKET_LABELS[market_type],
                    getattr(market_summary, 'title', None),
                    getattr(market_summary, 'body', None),
                    market_background,
                    market_key_themes,
                    market_outlook,
                ),
                raw_news_count=_market_news_count(
                    context.raw_news_count_by_market,
                    market_type,
                    context.raw_news_count,
                ),
                processed_news_count=_market_news_count(
                    context.processed_news_count_by_market,
                    market_type,
                    context.processed_news_count,
                ),
                cluster_count=len(by_market.get(market_type, [])),
                partial_message=None,
                metadata_json={},
            )
            for index_order, index in enumerate(
                indices_by_market.get(market_type, []), start=1
            ):
                await snapshot_repo.insert_page_market_index(
                    {
                        'page_market_id': page_market_id,
                        'market_index_daily_id': index.market_index_daily_id,
                        'source_date': index.source_date,
                        'expected_session_date': index.expected_session_date,
                        'session_close_at': index.session_close_at,
                        'display_order': index_order,
                        'index_code': index.index_code,
                        'index_name': index.index_name,
                        'close_price': index.close_price,
                        'change_value': index.change_value,
                        'change_percent': index.change_percent,
                        'high_price': index.high_price,
                        'low_price': index.low_price,
                        'currency_code': index.currency_code,
                    }
                )
            for cluster_order, cluster in enumerate(
                by_market.get(market_type, []), start=1
            ):
                card_summary = summary_by_type.get(
                    (
                        AiSummaryType.CLUSTER_CARD_SUMMARY.value,
                        market_type,
                        cluster['id'],
                    )
                )
                cluster_summary = (
                    getattr(card_summary, 'body', None) or cluster['summary_short']
                )
                cluster_article_links = _article_links_for_cluster(
                    cluster,
                    by_id=article_links_by_cluster_id,
                    by_uid=article_links_by_cluster_uid,
                )
                article_titles = [
                    title
                    for article_link in cluster_article_links
                    if isinstance(title := article_link.get('title'), str)
                ]
                representative_link = _representative_article_link(
                    cluster, cluster_article_links
                )
                representative_article_id = cluster.get(
                    'representative_article_id'
                ) or (
                    representative_link.get('processed_article_id')
                    if representative_link
                    else None
                )
                representative_title = cluster.get('representative_title') or (
                    representative_link.get('title') if representative_link else None
                )
                representative_publisher_name = cluster.get(
                    'representative_publisher_name'
                ) or (
                    representative_link.get('publisher_name')
                    if representative_link
                    else None
                )
                representative_published_at = cluster.get(
                    'representative_published_at'
                ) or (
                    representative_link.get('published_at')
                    if representative_link
                    else None
                )
                representative_origin_link = cluster.get(
                    'representative_origin_link'
                ) or (
                    representative_link.get('origin_link')
                    if representative_link
                    else None
                )
                representative_naver_link = cluster.get(
                    'representative_naver_link'
                ) or (
                    representative_link.get('naver_link')
                    if representative_link
                    else None
                )
                cluster_payload = {
                    'page_market_id': page_market_id,
                    'cluster_id': cluster['id'],
                    'cluster_uid': cluster['cluster_uid'],
                    'display_order': cluster_order,
                    'title': cluster['title'],
                    'summary': cluster_summary,
                    'search_document': _build_search_document(
                        cluster['title'],
                        cluster_summary,
                        representative_title,
                        article_titles,
                    ),
                    'article_count': cluster['article_count'],
                    'tags_json': cluster.get('tags_json') or [],
                    'representative_article_id': representative_article_id,
                    'representative_title': representative_title,
                    'representative_publisher_name': representative_publisher_name,
                    'representative_published_at': representative_published_at,
                    'representative_origin_link': representative_origin_link,
                    'representative_naver_link': representative_naver_link,
                }
                snapshot_cluster_id = await snapshot_repo.insert_page_market_cluster(
                    cluster_payload
                )
                snapshot_cluster_id = require_snapshot_cluster_id(snapshot_cluster_id)
                await snapshot_repo.insert_page_market_cluster_themes(
                    snapshot_cluster_id,
                    themes_by_cluster_id.get(cluster['id'], []),
                )
                if cluster['id'] in missing_theme_cluster_ids:
                    await repository.add_event(
                        job_id=context.job_id,
                        step_code=self.step_code,
                        level=EventLevel.WARN.value,
                        message='Cluster theme classification produced no assignment.',
                        context_json={
                            'clusterId': cluster['id'],
                            'reason': THEME_CLASSIFICATION_MISSING,
                        },
                    )
            for link_order, article_link in enumerate(
                article_links_by_market.get(market_type, []), start=1
            ):
                await snapshot_repo.insert_page_article_link(
                    {
                        'page_market_id': page_market_id,
                        'display_order': link_order,
                        'processed_article_id': article_link['processed_article_id'],
                        'cluster_id': article_link['cluster_id'],
                        'cluster_uid': article_link['cluster_uid'],
                        'cluster_title': article_link['cluster_title'],
                        'title': article_link['title'],
                        'publisher_name': article_link.get('publisher_name'),
                        'published_at': article_link.get('published_at'),
                        'origin_link': article_link['origin_link'],
                        'naver_link': article_link.get('naver_link'),
                    }
                )

        context.page_id = page_id
        context.page_version_no = version_no
        if page_status == PageStatus.PARTIAL.value:
            await repository.add_event(
                job_id=context.job_id,
                step_code=self.step_code,
                level=EventLevel.WARN.value,
                message='Page snapshot created with partial status.',
                context_json={
                    'warnings': sanitize_public_diagnostics(context.warning_messages),
                    'partialReasons': sanitize_public_diagnostics(
                        context.partial_reasons
                    ),
                },
            )
        context.log_messages.append(
            f'Built page snapshot pageId={page_id}, versionNo={version_no}.'
        )
        return context


__all__ = ['BuildPageSnapshotStep']
