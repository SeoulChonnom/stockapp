from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Mapping
from copy import deepcopy
from typing import Any

from app.batch.ai_output_contracts import KEY_POINT_FAILURE
from app.batch.ai_retry.models import AiRetryCounts, AiRetryPageResult
from app.batch.ai_retry.resolver import (
    is_successful_summary,
    resolve_effective_summaries,
)
from app.batch.ai_summary_targets import build_ai_summary_target_key
from app.batch.diagnostics import build_bounded_partial_message
from app.batch.normalizers import metadata_optional_string, metadata_string_list
from app.batch.snapshot_contract import require_snapshot_cluster_id
from app.batch.steps.build_page_snapshot import _build_search_document
from app.batch.steps.page_snapshot_cloner import clone_child_rows, clone_page_markets
from app.db.enums import AiSummaryType, PageStatus
from app.db.repositories.page_snapshot_repo import PageSnapshotRepository
from app.db.repositories.page_snapshot_write_repo import (
    PageSnapshotWriteRepository,
)
from app.db.repositories.projections import AiSummaryRecord


class AiRetryPageBuilder:
    """Clone a persisted page and overlay effective AI summary fields."""

    def __init__(
        self,
        *,
        source_page_repo_factory: Callable[[Any], Any] | None = None,
        snapshot_repo_factory: Callable[[Any], Any] | None = None,
    ) -> None:
        self._source_page_repo_factory: Callable[[Any], Any] = (
            source_page_repo_factory or PageSnapshotRepository
        )
        self._snapshot_repo_factory: Callable[[Any], Any] = (
            snapshot_repo_factory or PageSnapshotWriteRepository
        )

    async def build(
        self,
        *,
        session: Any,
        source_page_id: int,
        source_job_id: int,
        retry_job_id: int,
        summaries: list[AiSummaryRecord],
        counts: AiRetryCounts,
    ) -> AiRetryPageResult:
        if counts.recovered_count <= 0:
            raise ValueError('A retry page requires at least one recovered target.')

        source_repo = self._source_page_repo_factory(session)
        write_repo = self._snapshot_repo_factory(session)
        source_page = await source_repo.get_page_header_by_id(source_page_id)
        if source_page is None:
            raise LookupError('The persisted AI retry source page was not found.')
        source_markets = await source_repo.get_page_markets(source_page_id)
        if not source_markets:
            raise LookupError('The persisted AI retry source markets were not found.')

        source_market_ids = [market['id'] for market in source_markets]
        source_indices = await source_repo.get_page_indices(source_market_ids)
        source_clusters = await source_repo.get_page_clusters(source_market_ids)
        source_links = await source_repo.get_page_article_links(source_market_ids)
        source_cluster_themes = await source_repo.get_page_cluster_themes(
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
        article_titles_by_cluster_id: dict[int, list[str]] = defaultdict(list)
        article_titles_by_cluster_uid: dict[str, list[str]] = defaultdict(list)
        for link in source_links:
            title = link.get('title')
            if not isinstance(title, str):
                continue
            if link.get('cluster_id') is not None:
                article_titles_by_cluster_id[link['cluster_id']].append(title)
            if link.get('cluster_uid') is not None:
                article_titles_by_cluster_uid[str(link['cluster_uid'])].append(title)
        effective = resolve_effective_summaries(summaries)
        issues = _build_page_issues(source_page, effective)
        page_status = PageStatus.READY.value if not issues else PageStatus.PARTIAL.value
        partial_message = _partial_message(issues)
        metadata = deepcopy(source_page.get('metadata_json') or {})
        global_summary = effective.get(AiSummaryType.GLOBAL_HEADLINE.value)
        global_metadata = (
            global_summary.metadata_json if global_summary is not None else {}
        ) or {}
        key_points = (
            global_metadata.get('keyPoints')
            if isinstance(global_metadata, Mapping)
            else None
        )
        if isinstance(key_points, list):
            metadata['keyPoints'] = deepcopy(key_points)
        metadata['issues'] = issues
        metadata['aiRetry'] = {
            'sourceJobId': source_job_id,
            'sourcePageId': source_page_id,
            'retryJobId': retry_job_id,
            'recoveredCount': counts.recovered_count,
            'successCount': counts.success_count,
            'targetCount': counts.target_count,
        }

        version_no = await write_repo.get_next_version_no(source_page['business_date'])
        page_title = source_page['page_title']
        global_headline = (
            global_summary.title
            if global_summary is not None
            else source_page.get('global_headline')
        )
        page_id = await write_repo.create_page(
            business_date=source_page['business_date'],
            version_no=version_no,
            page_title=page_title,
            status=page_status,
            global_headline=global_headline,
            search_document=source_page['search_document'],
            partial_message=partial_message,
            raw_news_count=source_page['raw_news_count'],
            processed_news_count=source_page['processed_news_count'],
            cluster_count=source_page['cluster_count'],
            batch_job_id=retry_job_id,
            metadata_json=metadata,
        )

        market_types: dict[int, str] = {}

        def build_market_fields(source_market: dict[str, Any]) -> dict[str, Any]:
            market_type = source_market['market_type']
            market_types[source_market['id']] = market_type
            market_summary_candidate = effective.get(
                build_ai_summary_target_key(
                    AiSummaryType.MARKET_SUMMARY.value,
                    market_type=market_type,
                    cluster_id=None,
                )
            )
            market_summary = (
                market_summary_candidate
                if market_summary_candidate is not None
                and is_successful_summary(market_summary_candidate)
                else None
            )
            market_metadata = (
                market_summary.metadata_json if market_summary is not None else {}
            ) or {}
            fields: dict[str, Any] = {
                'market_type': market_type,
                'display_order': source_market['display_order'],
                'market_label': source_market['market_label'],
                'summary_title': (
                    market_summary.title
                    if market_summary is not None
                    else source_market.get('summary_title')
                ),
                'summary_body': (
                    market_summary.body
                    if market_summary is not None
                    else source_market.get('summary_body')
                ),
                'analysis_background_json': metadata_string_list(
                    market_metadata,
                    'background',
                    fallback=source_market.get('analysis_background_json') or [],
                ),
                'analysis_key_themes_json': metadata_string_list(
                    market_metadata,
                    'keyThemes',
                    fallback=source_market.get('analysis_key_themes_json') or [],
                ),
                'analysis_outlook': metadata_optional_string(
                    market_metadata,
                    'outlook',
                    fallback=source_market.get('analysis_outlook'),
                ),
                'raw_news_count': source_market['raw_news_count'],
                'processed_news_count': source_market['processed_news_count'],
                'cluster_count': source_market['cluster_count'],
                'partial_message': (
                    partial_message
                    if any(issue.get('marketType') == market_type for issue in issues)
                    else source_market.get('partial_message')
                ),
                'metadata_json': source_market.get('metadata_json') or {},
                'expected_session_date': source_market.get('expected_session_date'),
                'actual_index_source_date': source_market.get(
                    'actual_index_source_date'
                ),
                'session_close_at': source_market.get('session_close_at'),
                'news_window_start_at': source_market.get('news_window_start_at'),
                'news_window_end_at': source_market.get('news_window_end_at'),
                'news_coverage_complete': source_market.get('news_coverage_complete'),
            }

            # Keep the exact persisted document when no market summary was
            # overlaid.  A successful retry summary changes the searchable
            # fields, so rebuild the document from the effective values.
            if market_summary is None:
                fields['search_document'] = source_market['search_document']
            else:
                fields['search_document'] = _build_search_document(
                    source_market['market_label'],
                    fields['summary_title'],
                    fields['summary_body'],
                    fields['analysis_background_json'],
                    fields['analysis_key_themes_json'],
                    fields['analysis_outlook'],
                )
            return fields

        new_market_ids = await clone_page_markets(
            source_markets,
            page_id=page_id,
            snapshot_repo=write_repo,
            build_fields=build_market_fields,
        )

        def apply_card_summary(
            source_cluster: dict[str, Any], payload: dict[str, Any]
        ) -> dict[str, Any]:
            if source_cluster.get('cluster_id') is None:
                return payload
            card_summary = effective.get(
                build_ai_summary_target_key(
                    AiSummaryType.CLUSTER_CARD_SUMMARY.value,
                    market_type=market_types[source_cluster['page_market_id']],
                    cluster_id=source_cluster['cluster_id'],
                )
            )
            if card_summary is not None and is_successful_summary(card_summary):
                payload['summary'] = card_summary.body
            return payload

        await clone_child_rows(
            source_indices,
            new_market_ids=new_market_ids,
            insert_fn=write_repo.insert_page_market_index,
        )
        for source_cluster in source_clusters:
            payload = {
                key: value for key, value in source_cluster.items() if key != 'id'
            }
            payload['page_market_id'] = new_market_ids[source_cluster['page_market_id']]
            payload = apply_card_summary(source_cluster, payload)
            card_summary = effective.get(
                build_ai_summary_target_key(
                    AiSummaryType.CLUSTER_CARD_SUMMARY.value,
                    market_type=market_types[source_cluster['page_market_id']],
                    cluster_id=source_cluster.get('cluster_id'),
                )
            )
            if card_summary is not None and is_successful_summary(card_summary):
                cluster_id = source_cluster.get('cluster_id')
                article_titles = (
                    article_titles_by_cluster_id.get(cluster_id, [])
                    if cluster_id is not None
                    else article_titles_by_cluster_uid.get(
                        str(source_cluster.get('cluster_uid')), []
                    )
                )
                payload['search_document'] = _build_search_document(
                    source_cluster['title'],
                    payload.get('summary'),
                    source_cluster.get('representative_title'),
                    article_titles,
                )
            snapshot_cluster_id = await write_repo.insert_page_market_cluster(payload)
            snapshot_cluster_id = require_snapshot_cluster_id(snapshot_cluster_id)
            await write_repo.insert_page_market_cluster_themes(
                snapshot_cluster_id,
                themes_by_cluster_id.get(source_cluster.get('id'), []),
            )
        await clone_child_rows(
            source_links,
            new_market_ids=new_market_ids,
            insert_fn=write_repo.insert_page_article_link,
        )

        return AiRetryPageResult(
            page_id=page_id,
            version_no=version_no,
            status=page_status,
            partial_message=partial_message,
        )


def _build_page_issues(
    source_page: dict[str, Any],
    effective: dict[str, AiSummaryRecord],
) -> list[dict[str, Any]]:
    source_metadata = source_page.get('metadata_json') or {}
    raw_issues = source_metadata.get('issues')
    issues: list[dict[str, Any]] = []
    if isinstance(raw_issues, list):
        issues.extend(
            dict(issue)
            for issue in raw_issues
            if isinstance(issue, dict) and issue.get('category') != 'AI_SUMMARY'
        )
    else:
        warnings = source_metadata.get('warnings')
        if isinstance(warnings, list):
            issues.extend(
                {
                    'category': 'BATCH_WARNING',
                    'code': 'LEGACY_WARNING',
                    'message': warning,
                }
                for warning in warnings
                if isinstance(warning, str)
            )
        legacy_partial = source_page.get('partial_message')
        if (
            source_page.get('status') == PageStatus.PARTIAL.value
            and legacy_partial
            and not _is_ai_only_legacy_partial(legacy_partial)
            and not issues
        ):
            issues.append(
                {
                    'category': 'BATCH_PARTIAL',
                    'code': 'LEGACY_PARTIAL',
                    'message': legacy_partial,
                }
            )

    for target_key, summary in sorted(effective.items()):
        key_point_issue = _key_point_issue(target_key, summary)
        if key_point_issue is not None:
            issues.append(key_point_issue)
        if summary.summary_type == AiSummaryType.CLUSTER_DETAIL_ANALYSIS.value:
            continue
        if is_successful_summary(summary):
            continue
        issues.append(
            {
                'category': 'AI_SUMMARY',
                'code': 'AI_SUMMARY_UNRESOLVED',
                'targetKey': target_key,
                'marketType': summary.market_type,
                'message': (f'AI summary remains {summary.status}: {target_key}'),
            }
        )
    return issues


def _key_point_issue(
    target_key: str,
    summary: AiSummaryRecord,
) -> dict[str, str] | None:
    if target_key != AiSummaryType.GLOBAL_HEADLINE.value:
        return None
    metadata = summary.metadata_json or {}
    issue = metadata.get('keyPointIssue') if isinstance(metadata, Mapping) else None
    if not isinstance(issue, Mapping):
        return None
    if issue.get('code') != KEY_POINT_FAILURE['code']:
        return None
    return dict(KEY_POINT_FAILURE)


def _partial_message(issues: list[dict[str, Any]]) -> str | None:
    messages = [
        message for issue in issues if isinstance(message := issue.get('message'), str)
    ]
    return build_bounded_partial_message(messages)


def _is_ai_only_legacy_partial(message: str) -> bool:
    return message.startswith(('AI summary fallback', 'Fallback processing was used'))


__all__ = ['AiRetryPageBuilder']
