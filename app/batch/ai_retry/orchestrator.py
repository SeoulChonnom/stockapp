from __future__ import annotations

from collections.abc import Callable
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.batch.ai_retry.models import (
    AiRetryPageResult,
    AiRetryRunResult,
    AiRetrySelection,
)
from app.batch.ai_retry.page_builder import AiRetryPageBuilder
from app.batch.ai_retry.resolver import (
    calculate_retry_counts,
    select_retry_targets,
)
from app.batch.exceptions import BatchLeaseLostError
from app.batch.providers.llm_provider import PROMPT_VERSION, BatchLlmProvider
from app.batch.steps.generate_ai_summaries import (
    _generate_cluster_card_summary,
    _generate_cluster_detail_summary,
    _generate_global_headline,
    _generate_market_summary,
)
from app.db.enums import AiSummaryStatus, EventLevel
from app.db.repositories.ai_retry_repo import PostgresAiRetryRepository
from app.db.repositories.ai_summary_repo import AiSummaryRepository
from app.db.repositories.ai_summary_write_repo import AiSummaryWriteRepository
from app.db.repositories.batch_job_repo import BatchJobRepository
from app.db.repositories.cluster_repo import ClusterRepository
from app.db.repositories.market_index_repo import MarketIndexRepository
from app.db.repositories.projections import AiSummaryCreateParams, AiSummaryRecord
from app.db.session import get_session_maker

AI_RETRY_SELECT_STEP = 'AI_RETRY_SELECT'
AI_RETRY_GENERATE_STEP = 'AI_RETRY_GENERATE'
AI_RETRY_BUILD_PAGE_STEP = 'AI_RETRY_BUILD_PAGE'
AI_RETRY_FINALIZE_STEP = 'AI_RETRY_FINALIZE'


class AiRetryOrchestrator:
    """Resume-safe workflow that retries only unresolved AI summary targets."""

    def __init__(
        self,
        session_maker: async_sessionmaker | None = None,
        *,
        retry_repo_factory: Callable[[object], Any] | None = None,
        job_repo_factory: Callable[[object], Any] | None = None,
        summary_repo_factory: Callable[[object], Any] | None = None,
        summary_write_repo_factory: Callable[[object], Any] | None = None,
        cluster_repo_factory: Callable[[object], Any] | None = None,
        index_repo_factory: Callable[[object], Any] | None = None,
        llm_provider_factory: Callable[[], Any] | None = None,
        page_builder: AiRetryPageBuilder | None = None,
    ) -> None:
        self._session_maker = session_maker or get_session_maker()
        self._retry_repo_factory = retry_repo_factory or PostgresAiRetryRepository
        self._job_repo_factory = job_repo_factory
        self._summary_repo_factory = summary_repo_factory or AiSummaryRepository
        self._summary_write_repo_factory = (
            summary_write_repo_factory or AiSummaryWriteRepository
        )
        self._cluster_repo_factory = cluster_repo_factory or ClusterRepository
        self._index_repo_factory = index_repo_factory or MarketIndexRepository
        self._llm_provider_factory = llm_provider_factory or BatchLlmProvider
        self._page_builder = page_builder or AiRetryPageBuilder()

    async def run(
        self, job_id: int, lease_token: UUID | None = None
    ) -> AiRetryRunResult:
        async with self._session_maker() as session:
            retry_repo = self._retry_repo_factory(session)
            job_repo = self._make_job_repository(session, lease_token)
            summary_repo = self._summary_repo_factory(session)
            summary_write_repo = self._summary_write_repo_factory(session)
            try:
                job = await retry_repo.get_job(job_id)
                if job is None:
                    raise LookupError(f'AI retry job {job_id} was not found.')
                if job.run_mode != 'AI_RETRY' or job.source_job_id is None:
                    raise ValueError(f'Batch job {job_id} is not an AI_RETRY job.')

                await _begin_step(
                    job_repo,
                    job_id=job_id,
                    lease_token=lease_token,
                    step_code=AI_RETRY_SELECT_STEP,
                )
                lineage = await summary_repo.list_retry_lineage_summaries(
                    job.source_job_id
                )
                source_rows = [
                    row for row in lineage if row.batch_job_id == job.source_job_id
                ]
                if not source_rows:
                    raise LookupError(
                        'The source job has no persisted AI summary targets.'
                    )
                selections = select_retry_targets(
                    source_job_id=job.source_job_id,
                    retry_job_id=job_id,
                    lineage=lineage,
                )
                await _checkpoint(
                    job_repo,
                    job_id=job_id,
                    lease_token=lease_token,
                    current_step=AI_RETRY_SELECT_STEP,
                    completed_steps=[AI_RETRY_SELECT_STEP],
                    context={'selectedTargetCount': len(selections)},
                )
                await retry_repo.commit()

                clusters = await self._cluster_repo_factory(
                    session
                ).list_clusters_by_business_date(job.business_date)
                indices = await self._index_repo_factory(
                    session
                ).list_indices_by_business_date(job.business_date)
                llm_provider = self._llm_provider_factory()
                for selection in selections:
                    await _begin_step(
                        job_repo,
                        job_id=job_id,
                        lease_token=lease_token,
                        step_code=AI_RETRY_GENERATE_STEP,
                    )
                    payload = await _generate_target(
                        selection,
                        llm_provider=llm_provider,
                        cluster_repo=self._cluster_repo_factory(session),
                        clusters=clusters,
                        indices=indices,
                    )
                    persisted = await summary_write_repo.upsert_retry_summary(
                        _retry_create_params(
                            job_id=job_id,
                            business_date=job.business_date,
                            selection=selection,
                            payload=payload,
                        )
                    )
                    lineage = _replace_current_retry(lineage, persisted)
                    counts = calculate_retry_counts(
                        source_job_id=job.source_job_id,
                        retry_job_id=job_id,
                        lineage=lineage,
                    )
                    await _checkpoint(
                        job_repo,
                        job_id=job_id,
                        lease_token=lease_token,
                        current_step=AI_RETRY_GENERATE_STEP,
                        completed_steps=[
                            AI_RETRY_SELECT_STEP,
                            AI_RETRY_GENERATE_STEP,
                        ],
                        context={
                            'lastTargetKey': selection.target.target_key,
                            **_count_payload(counts),
                        },
                    )
                    await retry_repo.commit()

                counts = calculate_retry_counts(
                    source_job_id=job.source_job_id,
                    retry_job_id=job_id,
                    lineage=lineage,
                )
                page = _page_from_checkpoint(job.checkpoint_json)
                if counts.recovered_count > 0 and page is None:
                    if job.source_page_id is None:
                        raise LookupError(
                            'The AI retry job has no persisted source page.'
                        )
                    await _begin_step(
                        job_repo,
                        job_id=job_id,
                        lease_token=lease_token,
                        step_code=AI_RETRY_BUILD_PAGE_STEP,
                    )
                    page = await self._page_builder.build(
                        session=session,
                        source_page_id=job.source_page_id,
                        source_job_id=job.source_job_id,
                        retry_job_id=job_id,
                        summaries=lineage,
                        counts=counts,
                    )
                    await _checkpoint(
                        job_repo,
                        job_id=job_id,
                        lease_token=lease_token,
                        current_step=AI_RETRY_BUILD_PAGE_STEP,
                        completed_steps=[
                            AI_RETRY_SELECT_STEP,
                            AI_RETRY_GENERATE_STEP,
                            AI_RETRY_BUILD_PAGE_STEP,
                        ],
                        context={
                            'pageId': page.page_id,
                            'pageVersionNo': page.version_no,
                            'pageStatus': page.status,
                            'pagePartialMessage': page.partial_message,
                            **_count_payload(counts),
                        },
                    )
                    await retry_repo.commit()

                status, partial_message = _terminal_status(counts, page)
                await _begin_step(
                    job_repo,
                    job_id=job_id,
                    lease_token=lease_token,
                    step_code=AI_RETRY_FINALIZE_STEP,
                )
                completed = await retry_repo.complete_job(
                    job_id=job_id,
                    status=status,
                    counts=counts,
                    page_id=page.page_id if page else None,
                    page_version_no=page.version_no if page else None,
                    partial_message=partial_message,
                    log_summary=(
                        f'AI retry attempted {counts.attempted_count} target(s); '
                        f'recovered {counts.recovered_count}.'
                    ),
                    lease_token=lease_token,
                )
                if not completed:
                    raise BatchLeaseLostError(
                        f'Lease lost while finalizing AI retry job {job_id}.'
                    )
                await job_repo.add_event(
                    job_id=job_id,
                    step_code=AI_RETRY_FINALIZE_STEP,
                    level=EventLevel.INFO.value,
                    message=f'AI retry finalized with status={status}.',
                    context_json=_count_payload(counts),
                )
                await retry_repo.commit()
                return AiRetryRunResult(
                    counts=counts,
                    page=page,
                    status=status,
                    partial_message=partial_message,
                )
            except Exception:
                await retry_repo.rollback()
                raise

    def _make_job_repository(self, session: object, lease_token: UUID | None) -> Any:
        if self._job_repo_factory is not None:
            return self._job_repo_factory(session)
        try:
            return BatchJobRepository(session, lease_token=lease_token)
        except TypeError:
            return BatchJobRepository(session)


async def _generate_target(
    selection: AiRetrySelection,
    *,
    llm_provider: Any,
    cluster_repo: Any,
    clusters: list[dict[str, Any]],
    indices: list[Any],
) -> dict[str, Any]:
    target = selection.target
    if target.summary_type == 'GLOBAL_HEADLINE':
        return await _generate_global_headline(llm_provider, clusters, indices)
    if target.summary_type == 'MARKET_SUMMARY':
        market_clusters = [
            row for row in clusters if row['market_type'] == target.market_type
        ]
        market_indices = [
            row for row in indices if row.market_type == target.market_type
        ]
        return await _generate_market_summary(
            llm_provider,
            market_type=target.market_type,
            clusters=market_clusters,
            indices=market_indices,
        )

    cluster = next((row for row in clusters if row['id'] == target.cluster_id), None)
    if cluster is None:
        return {
            'title': selection.source_summary.title,
            'body': selection.source_summary.body,
            'paragraphs': selection.source_summary.paragraphs_json,
            'status': AiSummaryStatus.FAILED.value,
            'fallback_used': False,
            'error_message': f'Cluster {target.cluster_id} was not found.',
            'metadata_json': {'reason': 'retry_source_missing'},
        }
    memberships = await cluster_repo.get_cluster_articles(cluster['id'])
    articles = await cluster_repo.get_processed_articles(
        [row['processed_article_id'] for row in memberships]
    )
    if target.summary_type == 'CLUSTER_CARD_SUMMARY':
        return await _generate_cluster_card_summary(
            llm_provider,
            target.market_type,
            cluster,
            articles,
        )
    if target.summary_type == 'CLUSTER_DETAIL_ANALYSIS':
        return await _generate_cluster_detail_summary(
            llm_provider,
            target.market_type,
            cluster,
            articles,
        )
    raise ValueError(f'Unsupported retry target: {target.target_key}')


def _retry_create_params(
    *,
    job_id: int,
    business_date: Any,
    selection: AiRetrySelection,
    payload: dict[str, Any],
) -> AiSummaryCreateParams:
    existing = selection.existing_retry
    source_summary_id = (
        existing.source_summary_id
        if existing is not None
        else selection.source_summary.summary_id
    )
    attempt_no = (
        existing.attempt_no
        if existing is not None
        else selection.source_summary.attempt_no + 1
    )
    metadata = dict(payload.get('metadata_json') or {})
    metadata['retry'] = {
        'sourceSummaryId': source_summary_id,
        'attemptNo': attempt_no,
    }
    return AiSummaryCreateParams(
        batch_job_id=job_id,
        summary_type=selection.target.summary_type,
        business_date=business_date,
        market_type=selection.target.market_type,
        cluster_id=selection.target.cluster_id,
        title=payload.get('title'),
        body=payload.get('body'),
        paragraphs_json=payload.get('paragraphs', []),
        model_name=payload.get('model_name'),
        prompt_version=PROMPT_VERSION,
        status=payload['status'],
        fallback_used=payload['fallback_used'],
        error_message=payload.get('error_message'),
        metadata_json=metadata,
        target_key=selection.target.target_key,
        source_summary_id=source_summary_id,
        attempt_no=attempt_no,
    )


def _replace_current_retry(
    lineage: list[AiSummaryRecord],
    persisted: AiSummaryRecord,
) -> list[AiSummaryRecord]:
    return [
        row
        for row in lineage
        if not (
            row.batch_job_id == persisted.batch_job_id
            and row.target_key == persisted.target_key
        )
    ] + [persisted]


async def _begin_step(
    repository: Any,
    *,
    job_id: int,
    lease_token: UUID | None,
    step_code: str,
) -> None:
    method = getattr(repository, 'begin_step', None)
    if lease_token is None or method is None:
        return
    if not await method(
        job_id=job_id,
        lease_token=lease_token,
        step_code=step_code,
    ):
        raise BatchLeaseLostError(f'Lease lost before AI retry step {step_code}.')


async def _checkpoint(
    repository: Any,
    *,
    job_id: int,
    lease_token: UUID | None,
    current_step: str,
    completed_steps: list[str],
    context: dict[str, Any],
) -> None:
    method = getattr(repository, 'save_checkpoint', None)
    if lease_token is None or method is None:
        return
    saved = await method(
        job_id=job_id,
        lease_token=lease_token,
        current_step=current_step,
        checkpoint_json={
            'completedSteps': completed_steps,
            'context': context,
        },
    )
    if not saved:
        raise BatchLeaseLostError(
            f'Lease lost while checkpointing AI retry job {job_id}.'
        )


def _terminal_status(counts: Any, page: Any) -> tuple[str, str | None]:
    unresolved_count = counts.target_count - counts.success_count
    if unresolved_count:
        return (
            'PARTIAL',
            f'{unresolved_count} AI summary target(s) remain unresolved.',
        )
    if page is not None and page.status == 'PARTIAL':
        return 'PARTIAL', page.partial_message
    return 'SUCCESS', None


def _page_from_checkpoint(checkpoint: object) -> AiRetryPageResult | None:
    if not isinstance(checkpoint, dict):
        return None
    completed_steps = checkpoint.get('completedSteps')
    context = checkpoint.get('context')
    if (
        not isinstance(completed_steps, list)
        or AI_RETRY_BUILD_PAGE_STEP not in completed_steps
        or not isinstance(context, dict)
        or not isinstance(context.get('pageId'), int)
        or not isinstance(context.get('pageVersionNo'), int)
        or not isinstance(context.get('pageStatus'), str)
    ):
        return None
    return AiRetryPageResult(
        page_id=context['pageId'],
        version_no=context['pageVersionNo'],
        status=context['pageStatus'],
        partial_message=(
            context.get('pagePartialMessage')
            if isinstance(context.get('pagePartialMessage'), str)
            else None
        ),
    )


def _count_payload(counts: Any) -> dict[str, int]:
    return {
        'aiTargetCount': counts.target_count,
        'aiAttemptedCount': counts.attempted_count,
        'aiSuccessCount': counts.success_count,
        'aiFallbackCount': counts.fallback_count,
        'aiFailedCount': counts.failed_count,
        'aiRecoveredCount': counts.recovered_count,
    }


__all__ = [
    'AI_RETRY_BUILD_PAGE_STEP',
    'AI_RETRY_FINALIZE_STEP',
    'AI_RETRY_GENERATE_STEP',
    'AI_RETRY_SELECT_STEP',
    'AiRetryOrchestrator',
]
