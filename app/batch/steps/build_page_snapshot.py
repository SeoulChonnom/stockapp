from __future__ import annotations

from app.batch.models import BatchExecutionContext
from app.batch.steps.base import BatchStep
from app.db.enums import AiSummaryType, EventLevel, PageStatus
from app.db.repositories.ai_summary_repo import AiSummaryRepository
from app.db.repositories.batch_job_repo import BatchJobRepository
from app.db.repositories.cluster_repo import ClusterRepository
from app.db.repositories.market_index_repo import MarketIndexRepository
from app.db.repositories.page_snapshot_write_repo import PageSnapshotWriteRepository


class BuildPageSnapshotStep(BatchStep):
    step_code = 'BUILD_PAGE_SNAPSHOT'
    started_message = 'Build page snapshot step started.'
    completed_message = 'Build page snapshot step completed.'

    async def run(
        self,
        repository: BatchJobRepository,
        context: BatchExecutionContext,
    ) -> BatchExecutionContext:
        session = getattr(repository, 'session', None)
        if session is None or not hasattr(session, 'bind'):
            context.page_id = context.page_id or -1
            context.page_version_no = context.page_version_no or 1
            context.log_messages.append('Page snapshot build step is scaffolded.')
            return context

        cluster_repo = ClusterRepository(session)
        summary_repo = AiSummaryRepository(session)
        index_repo = MarketIndexRepository(session)
        snapshot_repo = PageSnapshotWriteRepository(session)

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

        if not clusters and not context.rebuild_page_only:
            context.error_code = 'SNAPSHOT_SOURCE_MISSING'
            context.error_message = '스냅샷 생성에 필요한 클러스터 데이터가 없습니다.'
            return context

        summary_by_type: dict[tuple[str, str | None, int | None], object] = {}
        for summary in summaries:
            summary_by_type[
                (summary.summary_type, summary.market_type, summary.cluster_id)
            ] = summary

        version_no = await snapshot_repo.get_next_version_no(context.business_date)
        page_status = (
            PageStatus.PARTIAL.value
            if context.partial_reasons or context.warning_messages
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
            metadata_json={'warnings': context.warning_messages},
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
            market_summary = summary_by_type.get(
                (AiSummaryType.MARKET_SUMMARY.value, market_type, None)
            )
            market_metadata = getattr(market_summary, 'metadata_json', {}) or {}
            page_market_id = await snapshot_repo.create_page_market(
                page_id=page_id,
                market_type=market_type,
                display_order=display_order,
                market_label='미국 증시 일간 요약'
                if market_type == 'US'
                else '한국 증시 일간 요약',
                summary_title=getattr(market_summary, 'title', None),
                summary_body=getattr(market_summary, 'body', None),
                analysis_background_json=list(market_metadata.get('background', [])),
                analysis_key_themes_json=list(market_metadata.get('keyThemes', [])),
                analysis_outlook=market_metadata.get('outlook'),
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


__all__ = ['BuildPageSnapshotStep']
