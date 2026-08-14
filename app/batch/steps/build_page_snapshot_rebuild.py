from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from typing import Any

from app.batch.models import BatchExecutionContext
from app.batch.normalizers import metadata_string_list
from app.batch.snapshot_contract import require_snapshot_cluster_id
from app.batch.steps.page_snapshot_cloner import clone_child_rows, clone_page_markets
from app.db.enums import EventLevel
from app.db.repositories.batch_job_repo import BatchJobRepository


def _build_rebuild_market_fields(source_market: dict[str, Any]) -> dict[str, Any]:
    fields: dict[str, Any] = {
        'market_type': source_market['market_type'],
        'display_order': source_market['display_order'],
        'market_label': source_market['market_label'],
        'summary_title': source_market.get('summary_title'),
        'summary_body': source_market.get('summary_body'),
        'analysis_background_json': source_market.get('analysis_background_json') or [],
        'analysis_key_themes_json': source_market.get('analysis_key_themes_json') or [],
        'analysis_outlook': source_market.get('analysis_outlook'),
        'raw_news_count': source_market['raw_news_count'],
        'processed_news_count': source_market['processed_news_count'],
        'cluster_count': source_market['cluster_count'],
        'partial_message': source_market.get('partial_message'),
        'metadata_json': source_market.get('metadata_json') or {},
        'search_document': source_market['search_document'],
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
            fields[snapshot_field] = source_market[snapshot_field]
    return fields


def _apply_rebuild_cluster_tags_fallback(
    source_cluster: dict[str, Any], payload: dict[str, Any]
) -> dict[str, Any]:
    payload['tags_json'] = source_cluster.get('tags_json') or []
    return payload


async def mark_rebuild_source_missing(
    repository: BatchJobRepository,
    context: BatchExecutionContext,
    *,
    step_code: str,
) -> BatchExecutionContext:
    context.error_code = 'SNAPSHOT_SOURCE_MISSING'
    context.error_message = '재생성할 기존 페이지 스냅샷 데이터가 없습니다.'
    await repository.add_event(
        job_id=context.job_id,
        step_code=step_code,
        level=EventLevel.WARN.value,
        message='Skipped page rebuild because persisted snapshot data is missing.',
        context_json={'businessDate': context.business_date.isoformat()},
    )
    return context


async def rebuild_page_snapshot_from_persisted_page(
    *,
    repository: BatchJobRepository,
    context: BatchExecutionContext,
    source_page_repo: Any,
    snapshot_repo: Any,
    step_code: str,
) -> BatchExecutionContext:
    if context.source_page_id is not None:
        source_page = await source_page_repo.get_page_header_by_id(
            context.source_page_id
        )
    else:
        source_page = await source_page_repo.get_page_header_by_business_date(
            context.business_date
        )
    if source_page is None:
        return await mark_rebuild_source_missing(
            repository, context, step_code=step_code
        )

    source_page_id = source_page['id']
    source_markets = await source_page_repo.get_page_markets(source_page_id)
    if not source_markets:
        return await mark_rebuild_source_missing(
            repository, context, step_code=step_code
        )

    source_market_ids = [market['id'] for market in source_markets]
    source_indices = await source_page_repo.get_page_indices(source_market_ids)
    source_clusters = await source_page_repo.get_page_clusters(source_market_ids)
    source_article_links = await source_page_repo.get_page_article_links(
        source_market_ids
    )
    source_cluster_themes = await source_page_repo.get_page_cluster_themes(
        [cluster['id'] for cluster in source_clusters]
    )
    themes_by_cluster_id: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for theme in source_cluster_themes:
        themes_by_cluster_id[theme['page_market_cluster_id']].append(
            {
                'theme_code': theme['theme_code'],
                'rank': theme['rank'],
            }
        )

    version_no = await snapshot_repo.get_next_version_no(context.business_date)
    page_title = source_page['page_title']
    global_headline = source_page.get('global_headline')
    page_id = await snapshot_repo.create_page(
        business_date=context.business_date,
        version_no=version_no,
        page_title=page_title,
        status=source_page['status'],
        global_headline=global_headline,
        search_document=source_page['search_document'],
        partial_message=source_page.get('partial_message'),
        raw_news_count=source_page['raw_news_count'],
        processed_news_count=source_page['processed_news_count'],
        cluster_count=source_page['cluster_count'],
        batch_job_id=context.job_id,
        metadata_json=deepcopy(source_page.get('metadata_json') or {}),
    )

    new_market_ids = await clone_page_markets(
        source_markets,
        page_id=page_id,
        snapshot_repo=snapshot_repo,
        build_fields=_build_rebuild_market_fields,
    )
    await clone_child_rows(
        source_indices,
        new_market_ids=new_market_ids,
        insert_fn=snapshot_repo.insert_page_market_index,
    )
    for source_cluster in source_clusters:
        payload = {key: value for key, value in source_cluster.items() if key != 'id'}
        payload['page_market_id'] = new_market_ids[source_cluster['page_market_id']]
        payload = _apply_rebuild_cluster_tags_fallback(source_cluster, payload)
        snapshot_cluster_id = await snapshot_repo.insert_page_market_cluster(payload)
        snapshot_cluster_id = require_snapshot_cluster_id(snapshot_cluster_id)
        await snapshot_repo.insert_page_market_cluster_themes(
            snapshot_cluster_id,
            themes_by_cluster_id.get(source_cluster.get('id'), []),
        )
    await clone_child_rows(
        source_article_links,
        new_market_ids=new_market_ids,
        insert_fn=snapshot_repo.insert_page_article_link,
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
        for warning in metadata_string_list(source_metadata, 'warnings')
        if warning not in context.warning_messages
    )
    context.log_messages.append(
        f'Rebuilt page snapshot pageId={page_id}, versionNo={version_no}, '
        f'sourcePageId={source_page_id}.'
    )
    return context


__all__ = ['rebuild_page_snapshot_from_persisted_page']
