from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.batch.models import BatchExecutionContext
from app.batch.steps.base import BatchStep, require_repository_session
from app.db.enums import AiSummaryType, EventLevel, PageStatus
from app.db.repositories.ai_summary_repo import AiSummaryRepository
from app.db.repositories.batch_job_repo import BatchJobRepository
from app.db.repositories.cluster_repo import ClusterRepository
from app.db.repositories.market_context_repo import MarketContextRepository
from app.db.repositories.market_index_repo import MarketIndexRepository
from app.db.repositories.page_snapshot_repo import PageSnapshotRepository
from app.db.repositories.page_snapshot_write_repo import PageSnapshotWriteRepository


def _metadata_string_list(metadata: dict[str, Any], key: str) -> list[str]:
    value = metadata.get(key)
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _metadata_optional_string(metadata: dict[str, Any], key: str) -> str | None:
    value = metadata.get(key)
    if isinstance(value, str):
        return value
    return None


def _structured_page_issues(
    context: BatchExecutionContext,
) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    for reason in context.partial_reasons:
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
        for warning in context.warning_messages
    )
    return issues


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
            return await self._rebuild_from_persisted_page(
                repository=repository,
                context=context,
                source_page_repo=self._source_page_repo_factory(session),
                snapshot_repo=snapshot_repo,
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
        market_contexts = {
            row.market_type: row
            for row in await self._context_repo_factory(session).list_for_job(
                context.job_id
            )
        }
        missing_market_contexts = [
            market_type
            for market_type in ('US', 'KR')
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

        if not context.partial_message:
            partial_messages = [
                *context.partial_reasons,
                *context.warning_messages,
            ]
            if partial_messages:
                context.partial_message = '; '.join(partial_messages[:3])

        summary_by_type: dict[tuple[str, str | None, int | None], object] = {}
        for summary in summaries:
            summary_by_type[
                (summary.summary_type, summary.market_type, summary.cluster_id)
            ] = summary

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
        global_headline_summary = summary_by_type.get(
            (AiSummaryType.GLOBAL_HEADLINE.value, None, None)
        )
        page_id = await snapshot_repo.create_page(
            business_date=context.business_date,
            version_no=version_no,
            page_title=f'글로벌 시장 일간 요약 - {context.business_date.isoformat()}',
            status=page_status,
            global_headline=getattr(global_headline_summary, 'title', None),
            partial_message=context.partial_message,
            raw_news_count=context.raw_news_count,
            processed_news_count=context.processed_news_count,
            cluster_count=context.cluster_count,
            batch_job_id=context.job_id,
            metadata_json={
                'warnings': context.warning_messages,
                'issues': _structured_page_issues(context),
            },
        )
        by_market: dict[str, list[dict]] = {'US': [], 'KR': []}
        for cluster in clusters:
            by_market.setdefault(cluster['market_type'], []).append(cluster)
        article_links_by_market: dict[str, list[dict]] = {'US': [], 'KR': []}
        for article_link in cluster_article_links:
            article_links_by_market.setdefault(article_link['market_type'], []).append(
                article_link
            )
        indices_by_market: dict[str, list] = {'US': [], 'KR': []}
        for index in indices:
            indices_by_market.setdefault(index.market_type, []).append(index)

        for display_order, market_type in enumerate(['US', 'KR'], start=1):
            market_context = market_contexts[market_type]
            market_summary = summary_by_type.get(
                (AiSummaryType.MARKET_SUMMARY.value, market_type, None)
            )
            market_metadata = getattr(market_summary, 'metadata_json', {}) or {}
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
                market_label='미국 증시 일간 요약'
                if market_type == 'US'
                else '한국 증시 일간 요약',
                summary_title=getattr(market_summary, 'title', None),
                summary_body=getattr(market_summary, 'body', None),
                analysis_background_json=_metadata_string_list(
                    market_metadata, 'background'
                ),
                analysis_key_themes_json=_metadata_string_list(
                    market_metadata, 'keyThemes'
                ),
                analysis_outlook=_metadata_optional_string(market_metadata, 'outlook'),
                raw_news_count=context.raw_news_count,
                processed_news_count=context.processed_news_count,
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
                await snapshot_repo.insert_page_market_cluster(
                    {
                        'page_market_id': page_market_id,
                        'cluster_id': cluster['id'],
                        'cluster_uid': cluster['cluster_uid'],
                        'display_order': cluster_order,
                        'title': cluster['title'],
                        'summary': getattr(card_summary, 'body', None)
                        or cluster['summary_short'],
                        'article_count': cluster['article_count'],
                        'tags_json': cluster.get('tags_json') or [],
                        'representative_article_id': cluster[
                            'representative_article_id'
                        ],
                        'representative_title': cluster.get('representative_title'),
                        'representative_publisher_name': cluster.get(
                            'representative_publisher_name'
                        ),
                        'representative_published_at': cluster.get(
                            'representative_published_at'
                        ),
                        'representative_origin_link': cluster.get(
                            'representative_origin_link'
                        ),
                        'representative_naver_link': cluster.get(
                            'representative_naver_link'
                        ),
                    }
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
                    'warnings': context.warning_messages,
                    'partialReasons': context.partial_reasons,
                },
            )
        context.log_messages.append(
            f'Built page snapshot pageId={page_id}, versionNo={version_no}.'
        )
        return context

    async def _rebuild_from_persisted_page(
        self,
        *,
        repository: BatchJobRepository,
        context: BatchExecutionContext,
        source_page_repo: Any,
        snapshot_repo: Any,
    ) -> BatchExecutionContext:
        source_page = await source_page_repo.get_page_header_by_business_date(
            context.business_date
        )
        if source_page is None:
            return await self._mark_rebuild_source_missing(repository, context)

        source_page_id = source_page['id']
        source_markets = await source_page_repo.get_page_markets(source_page_id)
        if not source_markets:
            return await self._mark_rebuild_source_missing(repository, context)

        source_market_ids = [market['id'] for market in source_markets]
        source_indices = await source_page_repo.get_page_indices(source_market_ids)
        source_clusters = await source_page_repo.get_page_clusters(source_market_ids)
        source_article_links = await source_page_repo.get_page_article_links(
            source_market_ids
        )

        version_no = await snapshot_repo.get_next_version_no(context.business_date)
        page_id = await snapshot_repo.create_page(
            business_date=context.business_date,
            version_no=version_no,
            page_title=source_page['page_title'],
            status=source_page['status'],
            global_headline=source_page.get('global_headline'),
            partial_message=source_page.get('partial_message'),
            raw_news_count=source_page['raw_news_count'],
            processed_news_count=source_page['processed_news_count'],
            cluster_count=source_page['cluster_count'],
            batch_job_id=context.job_id,
            metadata_json=source_page.get('metadata_json') or {},
        )

        new_market_ids: dict[int, int] = {}
        for source_market in source_markets:
            market_snapshot = {
                'page_id': page_id,
                'market_type': source_market['market_type'],
                'display_order': source_market['display_order'],
                'market_label': source_market['market_label'],
                'summary_title': source_market.get('summary_title'),
                'summary_body': source_market.get('summary_body'),
                'analysis_background_json': source_market.get(
                    'analysis_background_json'
                )
                or [],
                'analysis_key_themes_json': source_market.get(
                    'analysis_key_themes_json'
                )
                or [],
                'analysis_outlook': source_market.get('analysis_outlook'),
                'raw_news_count': source_market['raw_news_count'],
                'processed_news_count': source_market['processed_news_count'],
                'cluster_count': source_market['cluster_count'],
                'partial_message': source_market.get('partial_message'),
                'metadata_json': source_market.get('metadata_json') or {},
            }
            for snapshot_field in (
                'expected_session_date',
                'actual_index_source_date',
                'session_close_at',
                'news_window_start_at',
                'news_window_end_at',
                'news_coverage_complete',
            ):
                if snapshot_field in source_market:
                    market_snapshot[snapshot_field] = source_market[snapshot_field]
            new_market_ids[
                source_market['id']
            ] = await snapshot_repo.create_page_market(**market_snapshot)

        for source_index in source_indices:
            index_snapshot = {
                'page_market_id': new_market_ids[source_index['page_market_id']],
                'market_index_daily_id': source_index['market_index_daily_id'],
                'display_order': source_index['display_order'],
                'index_code': source_index['index_code'],
                'index_name': source_index['index_name'],
                'close_price': source_index['close_price'],
                'change_value': source_index['change_value'],
                'change_percent': source_index['change_percent'],
                'high_price': source_index['high_price'],
                'low_price': source_index['low_price'],
                'currency_code': source_index['currency_code'],
            }
            for snapshot_field in (
                'source_date',
                'expected_session_date',
                'session_close_at',
            ):
                if snapshot_field in source_index:
                    index_snapshot[snapshot_field] = source_index[snapshot_field]
            await snapshot_repo.insert_page_market_index(index_snapshot)

        for source_cluster in source_clusters:
            await snapshot_repo.insert_page_market_cluster(
                {
                    'page_market_id': new_market_ids[source_cluster['page_market_id']],
                    'cluster_id': source_cluster['cluster_id'],
                    'cluster_uid': source_cluster['cluster_uid'],
                    'display_order': source_cluster['display_order'],
                    'title': source_cluster['title'],
                    'summary': source_cluster.get('summary'),
                    'article_count': source_cluster['article_count'],
                    'tags_json': source_cluster.get('tags_json') or [],
                    'representative_article_id': source_cluster.get(
                        'representative_article_id'
                    ),
                    'representative_title': source_cluster.get('representative_title'),
                    'representative_publisher_name': source_cluster.get(
                        'representative_publisher_name'
                    ),
                    'representative_published_at': source_cluster.get(
                        'representative_published_at'
                    ),
                    'representative_origin_link': source_cluster.get(
                        'representative_origin_link'
                    ),
                    'representative_naver_link': source_cluster.get(
                        'representative_naver_link'
                    ),
                }
            )

        for source_link in source_article_links:
            await snapshot_repo.insert_page_article_link(
                {
                    'page_market_id': new_market_ids[source_link['page_market_id']],
                    'display_order': source_link['display_order'],
                    'processed_article_id': source_link['processed_article_id'],
                    'cluster_id': source_link['cluster_id'],
                    'cluster_uid': source_link['cluster_uid'],
                    'cluster_title': source_link['cluster_title'],
                    'title': source_link['title'],
                    'publisher_name': source_link.get('publisher_name'),
                    'published_at': source_link.get('published_at'),
                    'origin_link': source_link['origin_link'],
                    'naver_link': source_link.get('naver_link'),
                }
            )

        context.raw_news_count = int(source_page['raw_news_count'])
        context.processed_news_count = int(source_page['processed_news_count'])
        context.cluster_count = int(source_page['cluster_count'])
        context.partial_message = source_page.get('partial_message')
        context.page_id = page_id
        context.page_version_no = version_no
        source_metadata = source_page.get('metadata_json') or {}
        context.warning_messages.extend(
            warning
            for warning in _metadata_string_list(source_metadata, 'warnings')
            if warning not in context.warning_messages
        )
        context.log_messages.append(
            f'Rebuilt page snapshot pageId={page_id}, versionNo={version_no}, '
            f'sourcePageId={source_page_id}.'
        )
        return context

    async def _mark_rebuild_source_missing(
        self,
        repository: BatchJobRepository,
        context: BatchExecutionContext,
    ) -> BatchExecutionContext:
        context.error_code = 'SNAPSHOT_SOURCE_MISSING'
        context.error_message = '재생성할 기존 페이지 스냅샷 데이터가 없습니다.'
        await repository.add_event(
            job_id=context.job_id,
            step_code=self.step_code,
            level=EventLevel.WARN.value,
            message='Skipped page rebuild because persisted snapshot data is missing.',
            context_json={'businessDate': context.business_date.isoformat()},
        )
        return context


__all__ = ['BuildPageSnapshotStep']
