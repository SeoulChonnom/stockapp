from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.batch.ai_retry.models import AiRetryCounts, AiRetryPageResult
from app.batch.ai_retry.resolver import (
    is_successful_summary,
    resolve_effective_summaries,
)
from app.batch.ai_summary_targets import build_ai_summary_target_key
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
        effective = resolve_effective_summaries(summaries)
        issues = _build_page_issues(source_page, effective)
        non_ai_issues = [
            issue for issue in issues if issue.get('category') != 'AI_SUMMARY'
        ]
        all_targets_recovered = counts.success_count == counts.target_count
        page_status = (
            PageStatus.READY.value
            if all_targets_recovered and not non_ai_issues
            else PageStatus.PARTIAL.value
        )
        partial_message = _partial_message(issues)
        metadata = dict(source_page.get('metadata_json') or {})
        metadata['issues'] = issues
        metadata['aiRetry'] = {
            'sourceJobId': source_job_id,
            'sourcePageId': source_page_id,
            'retryJobId': retry_job_id,
            'recoveredCount': counts.recovered_count,
            'successCount': counts.success_count,
            'targetCount': counts.target_count,
        }

        global_summary = effective.get(AiSummaryType.GLOBAL_HEADLINE.value)
        version_no = await write_repo.get_next_version_no(source_page['business_date'])
        page_id = await write_repo.create_page(
            business_date=source_page['business_date'],
            version_no=version_no,
            page_title=source_page['page_title'],
            status=page_status,
            global_headline=(
                global_summary.title
                if global_summary is not None
                else source_page.get('global_headline')
            ),
            partial_message=partial_message,
            raw_news_count=source_page['raw_news_count'],
            processed_news_count=source_page['processed_news_count'],
            cluster_count=source_page['cluster_count'],
            batch_job_id=retry_job_id,
            metadata_json=metadata,
        )

        new_market_ids: dict[int, int] = {}
        market_types: dict[int, str] = {}
        for source_market in source_markets:
            market_type = source_market['market_type']
            market_types[source_market['id']] = market_type
            market_summary = effective.get(
                build_ai_summary_target_key(
                    AiSummaryType.MARKET_SUMMARY.value,
                    market_type=market_type,
                    cluster_id=None,
                )
            )
            market_metadata = (
                market_summary.metadata_json if market_summary is not None else {}
            ) or {}
            new_market_ids[source_market['id']] = await write_repo.create_page_market(
                page_id=page_id,
                market_type=market_type,
                display_order=source_market['display_order'],
                market_label=source_market['market_label'],
                summary_title=(
                    market_summary.title
                    if market_summary is not None
                    else source_market.get('summary_title')
                ),
                summary_body=(
                    market_summary.body
                    if market_summary is not None
                    else source_market.get('summary_body')
                ),
                analysis_background_json=_metadata_string_list(
                    market_metadata,
                    'background',
                    fallback=source_market.get('analysis_background_json') or [],
                ),
                analysis_key_themes_json=_metadata_string_list(
                    market_metadata,
                    'keyThemes',
                    fallback=source_market.get('analysis_key_themes_json') or [],
                ),
                analysis_outlook=_metadata_string(
                    market_metadata,
                    'outlook',
                    fallback=source_market.get('analysis_outlook'),
                ),
                raw_news_count=source_market['raw_news_count'],
                processed_news_count=source_market['processed_news_count'],
                cluster_count=source_market['cluster_count'],
                partial_message=(
                    partial_message
                    if any(issue.get('marketType') == market_type for issue in issues)
                    else source_market.get('partial_message')
                ),
                metadata_json=source_market.get('metadata_json') or {},
                expected_session_date=source_market.get('expected_session_date'),
                actual_index_source_date=source_market.get('actual_index_source_date'),
                session_close_at=source_market.get('session_close_at'),
                news_window_start_at=source_market.get('news_window_start_at'),
                news_window_end_at=source_market.get('news_window_end_at'),
                news_coverage_complete=source_market.get('news_coverage_complete'),
            )

        for source_index in source_indices:
            await write_repo.insert_page_market_index(
                {key: value for key, value in source_index.items() if key != 'id'}
                | {'page_market_id': new_market_ids[source_index['page_market_id']]}
            )

        for source_cluster in source_clusters:
            card_summary = None
            if source_cluster.get('cluster_id') is not None:
                card_summary = effective.get(
                    build_ai_summary_target_key(
                        AiSummaryType.CLUSTER_CARD_SUMMARY.value,
                        market_type=market_types[source_cluster['page_market_id']],
                        cluster_id=source_cluster['cluster_id'],
                    )
                )
            payload = {
                key: value for key, value in source_cluster.items() if key != 'id'
            }
            payload['page_market_id'] = new_market_ids[source_cluster['page_market_id']]
            if card_summary is not None:
                payload['summary'] = card_summary.body
            await write_repo.insert_page_market_cluster(payload)

        for source_link in source_links:
            await write_repo.insert_page_article_link(
                {key: value for key, value in source_link.items() if key != 'id'}
                | {'page_market_id': new_market_ids[source_link['page_market_id']]}
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


def _partial_message(issues: list[dict[str, Any]]) -> str | None:
    messages = [
        message
        for issue in issues
        if isinstance(message := issue.get('message'), str)
    ]
    return '; '.join(messages[:3]) if messages else None


def _is_ai_only_legacy_partial(message: str) -> bool:
    return message.startswith(('AI summary fallback', 'Fallback processing was used'))


def _metadata_string_list(
    metadata: dict[str, Any],
    key: str,
    *,
    fallback: list[str],
) -> list[str]:
    value = metadata.get(key)
    if not isinstance(value, list):
        return fallback
    strings = [item for item in value if isinstance(item, str)]
    return strings or fallback


def _metadata_string(
    metadata: dict[str, Any],
    key: str,
    *,
    fallback: str | None,
) -> str | None:
    value = metadata.get(key)
    return value if isinstance(value, str) else fallback


__all__ = ['AiRetryPageBuilder']
