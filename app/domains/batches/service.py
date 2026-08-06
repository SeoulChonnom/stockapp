from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta

from sqlalchemy.exc import IntegrityError  # pyright: ignore[reportMissingImports]

from app.batch.providers.naver_news import NAVER_NEWS_PROVIDER_NAME
from app.core.exceptions import ConflictError, NotFoundError
from app.core.settings import get_settings
from app.core.timezone import KST, get_business_date
from app.db.enums import (
    BatchJobStatus,
    BatchJobType,
    BatchRunMode,
    BatchTriggerType,
    derive_batch_job_type,
)
from app.db.repositories.ai_retry_repo import (
    AiRetryEnqueuePort,
    AiRetryIdempotencyConflictError,
    PostgresAiRetryRepository,
)
from app.db.repositories.batch_job_repo import BatchJobRepository
from app.db.repositories.news_collection_run_repo import NewsCollectionRunRepository
from app.db.repositories.projections import (
    BatchJobCreateParams,
    BatchJobRecord,
    NewsCollectionRunRecord,
)
from app.domains.batches.assembler import (
    build_ai_retry_run_payload,
    build_batch_job_detail_payload,
    build_batch_job_list_payload,
    build_batch_run_payload,
)

LOGGER = logging.getLogger(__name__)

_BATCH_JOB_INTEGRITY_CONSTRAINTS_ALREADY_RUNNING = {
    'uq_batch_job_one_active_market_daily_per_day',
    'uq_batch_job_idempotency_key',
}


def _integrity_constraint_name(exc: IntegrityError) -> str | None:
    """Best-effort extraction of the violated constraint name from a DB error."""
    diag = getattr(exc.orig, 'diag', None)
    return getattr(diag, 'constraint_name', None)


class BatchesService:
    def __init__(
        self,
        repository: BatchJobRepository,
        *,
        max_attempts: int = 3,
        ai_retry_enqueuer: AiRetryEnqueuePort | None = None,
        news_collection_repository: NewsCollectionRunRepository | None = None,
        now_factory: Callable[[], datetime] | None = None,
    ) -> None:
        self._repo = repository
        self._max_attempts = max_attempts
        self._ai_retry_enqueuer = ai_retry_enqueuer
        self._news_collection_repo = news_collection_repository
        self._now_factory = now_factory or (lambda: datetime.now(UTC))

    async def start_naver_news_collection(
        self,
        *,
        user_id: str | None,
        slot_end_at: datetime | None = None,
    ) -> dict[str, object]:
        settings = get_settings()
        window_start_at, window_end_at = resolve_news_collection_slot(
            now=self._now_factory(),
            requested_slot_end_at=slot_end_at,
            max_backfill_days=settings.naver_news_collection_backfill_max_days,
        )
        query_start_at = window_start_at - timedelta(
            minutes=settings.naver_news_collection_overlap_minutes
        )
        run_repo = self._news_collection_repo or NewsCollectionRunRepository(
            self._repo.session
        )
        existing_run = await run_repo.get_by_window(
            provider_name=NAVER_NEWS_PROVIDER_NAME,
            window_start_at=window_start_at,
            window_end_at=window_end_at,
        )
        if existing_run is not None:
            existing_job = await self._repo.get_job_by_id(existing_run.batch_job_id)
            if existing_job is None:
                raise RuntimeError(
                    'News collection run references a missing batch job.'
                )
            return _build_news_collection_payload(existing_job, existing_run, False)

        idempotency_key = (
            f'naver-news:{window_start_at.isoformat()}:{window_end_at.isoformat()}'
        )
        try:
            job = await self._repo.create_job(
                BatchJobCreateParams(
                    job_name='naver_news_collection',
                    business_date=window_end_at.astimezone(KST).date(),
                    status=BatchJobStatus.PENDING.value,
                    trigger_type=BatchTriggerType.SCHEDULED.value,
                    triggered_by_user_id=user_id,
                    force_run=False,
                    rebuild_page_only=False,
                    run_mode=BatchRunMode.NEWS_COLLECTION.value,
                    idempotency_key=idempotency_key,
                    max_attempts=self._max_attempts,
                )
            )
            run = await run_repo.create_run(
                batch_job_id=job.job_id,
                provider_name=NAVER_NEWS_PROVIDER_NAME,
                window_start_at=window_start_at,
                window_end_at=window_end_at,
                query_start_at=query_start_at,
                query_end_at=window_end_at,
            )
        except IntegrityError as exc:
            await self._repo.rollback()
            existing_run = await run_repo.get_by_window(
                provider_name=NAVER_NEWS_PROVIDER_NAME,
                window_start_at=window_start_at,
                window_end_at=window_end_at,
            )
            if existing_run is None:
                raise
            existing_job = await self._repo.get_job_by_id(existing_run.batch_job_id)
            if existing_job is None:
                raise RuntimeError(
                    'News collection run references a missing batch job.'
                ) from exc
            return _build_news_collection_payload(existing_job, existing_run, False)

        await self._repo.add_event(
            job_id=job.job_id,
            step_code='NEWS_COLLECTION_ENQUEUE',
            level='INFO',
            message='Naver incremental news collection requested.',
            context_json={
                'providerName': NAVER_NEWS_PROVIDER_NAME,
                'windowStartAt': window_start_at.isoformat(),
                'windowEndAt': window_end_at.isoformat(),
                'queryStartAt': query_start_at.isoformat(),
                'queryEndAt': window_end_at.isoformat(),
            },
        )
        await self._repo.commit()
        return _build_news_collection_payload(job, run, True)

    async def list_jobs(
        self,
        *,
        from_date: date | None,
        to_date: date | None,
        status: str | None,
        job_type: str | None = None,
        page: int,
        size: int,
    ) -> dict[str, object]:
        result = await self._repo.list_jobs(
            from_date=from_date,
            to_date=to_date,
            status=status,
            job_type=job_type,
            page=page,
            size=size,
        )
        return build_batch_job_list_payload(result)

    async def get_job_detail(self, job_id: int) -> dict[str, object]:
        job = await self._repo.get_job_by_id(job_id)
        if job is None:
            raise NotFoundError(
                'BATCH_JOB_NOT_FOUND', '요청한 배치 작업을 찾을 수 없습니다.'
            )
        news_run = None
        if derive_batch_job_type(job.run_mode) == BatchJobType.NEWS_COLLECTION:
            run_repo = self._news_collection_repo or NewsCollectionRunRepository(
                self._repo.session
            )
            news_run = await run_repo.get_by_job_id(job_id)
        return build_batch_job_detail_payload(job, news_run)

    async def start_market_daily_batch(
        self,
        *,
        business_date: date | None,
        user_id: str | None,
        force: bool,
        rebuild_page_only: bool,
        idempotency_key: str | None = None,
    ) -> dict[str, object]:
        resolved_business_date = business_date or get_business_date()
        normalized_idempotency_key = (
            idempotency_key.strip() if idempotency_key is not None else None
        )
        if normalized_idempotency_key == '':
            normalized_idempotency_key = None
        run_mode = (
            BatchRunMode.PAGE_REBUILD.value
            if rebuild_page_only
            else BatchRunMode.FULL.value
        )
        if normalized_idempotency_key is not None:
            existing_job = await self._repo.get_job_by_idempotency_key(
                normalized_idempotency_key
            )
            if existing_job is not None:
                return await self._reuse_idempotent_job(
                    existing_job,
                    business_date=resolved_business_date,
                    run_mode=run_mode,
                    force=force,
                )

        if await self._repo.has_active_job_for_business_date(resolved_business_date):
            raise ConflictError(
                'BATCH_ALREADY_RUNNING',
                '동일 날짜의 배치가 이미 실행 중입니다.',
            )
        page_source = await self._repo.get_latest_page_source(resolved_business_date)
        if rebuild_page_only and page_source is None:
            raise NotFoundError(
                'PAGE_NOT_FOUND',
                '재생성할 기존 페이지를 찾을 수 없습니다.',
            )
        if not rebuild_page_only and not force and page_source is not None:
            LOGGER.info(
                'Rejecting market daily batch start because a page already '
                'exists for business_date=%s: page_id=%s status=%s',
                resolved_business_date,
                page_source.page_id,
                page_source.status,
            )
            raise ConflictError(
                'PAGE_ALREADY_EXISTS',
                '이미 생성된 페이지가 있어 배치를 시작할 수 없습니다. '
                f'(기존 페이지 상태: {page_source.status})',
            )

        try:
            job = await self._repo.create_job(
                BatchJobCreateParams(
                    business_date=resolved_business_date,
                    status=BatchJobStatus.PENDING.value,
                    trigger_type=(
                        BatchTriggerType.ADMIN_REBUILD.value
                        if rebuild_page_only
                        else BatchTriggerType.MANUAL.value
                    ),
                    triggered_by_user_id=user_id,
                    force_run=force,
                    rebuild_page_only=rebuild_page_only,
                    run_mode=run_mode,
                    source_job_id=(
                        page_source.batch_job_id
                        if rebuild_page_only and page_source is not None
                        else None
                    ),
                    source_page_id=(
                        page_source.page_id
                        if rebuild_page_only and page_source is not None
                        else None
                    ),
                    idempotency_key=normalized_idempotency_key,
                    max_attempts=self._max_attempts,
                )
            )
        except IntegrityError as exc:
            if normalized_idempotency_key is not None:
                existing_job = await self._repo.get_job_by_idempotency_key(
                    normalized_idempotency_key
                )
                if existing_job is not None:
                    return await self._reuse_idempotent_job(
                        existing_job,
                        business_date=resolved_business_date,
                        run_mode=run_mode,
                        force=force,
                    )
            constraint_name = _integrity_constraint_name(exc)
            if (
                constraint_name is not None
                and constraint_name
                not in _BATCH_JOB_INTEGRITY_CONSTRAINTS_ALREADY_RUNNING
            ):
                LOGGER.error(
                    'Unexpected integrity error while creating market daily '
                    'batch job: constraint=%s',
                    constraint_name,
                    exc_info=exc,
                )
                raise
            if constraint_name is None:
                LOGGER.warning(
                    'Integrity error while creating market daily batch job '
                    'did not report a constraint name; assuming a '
                    'concurrent run for the same business date.',
                    exc_info=exc,
                )
            raise ConflictError(
                'BATCH_ALREADY_RUNNING',
                '동일 날짜의 배치가 이미 실행 중입니다.',
            ) from exc
        await self._repo.add_event(
            job_id=job.job_id,
            step_code='CREATE_JOB',
            level='INFO',
            message='Manual market daily batch requested.',
            context_json={
                'force': force,
                'rebuildPageOnly': rebuild_page_only,
                'runMode': run_mode,
            },
        )
        await self._repo.commit()
        return {
            **build_batch_run_payload(job),
            '_created': True,
        }

    async def _reuse_idempotent_job(
        self,
        existing_job: BatchJobRecord,
        *,
        business_date: date,
        run_mode: str,
        force: bool,
    ) -> dict[str, object]:
        _validate_idempotent_replay(
            existing_job,
            business_date=business_date,
            run_mode=run_mode,
            force=force,
        )
        if existing_job.status == BatchJobStatus.FAILED.value:
            try:
                retried_job = await self._repo.retry_failed_job(existing_job.job_id)
            except IntegrityError as exc:
                await self._repo.rollback()
                raise ConflictError(
                    'BATCH_ALREADY_RUNNING',
                    '동일 날짜의 배치가 이미 실행 중입니다.',
                ) from exc
            if retried_job is not None:
                await self._repo.add_event(
                    job_id=retried_job.job_id,
                    step_code='RETRY_JOB',
                    level='INFO',
                    message=(
                        'Failed market daily batch requeued via idempotent replay.'
                    ),
                )
                await self._repo.commit()
                return {
                    **build_batch_run_payload(retried_job),
                    '_created': True,
                }
        return {
            **build_batch_run_payload(existing_job),
            '_created': False,
        }

    async def retry_ai_summaries(
        self,
        *,
        requested_job_id: int,
        user_id: str | None,
        idempotency_key: str | None,
    ) -> dict[str, object]:
        enqueuer = self._ai_retry_enqueuer
        if enqueuer is None:
            enqueuer = PostgresAiRetryRepository(
                self._repo.session,
                max_attempts=self._max_attempts,
            )
        source = await enqueuer.resolve_source(requested_job_id)
        if source is None:
            raise NotFoundError(
                'BATCH_JOB_NOT_FOUND',
                '요청한 배치 작업을 찾을 수 없습니다.',
            )
        if source.source_status in {
            BatchJobStatus.PENDING.value,
            BatchJobStatus.RUNNING.value,
        }:
            raise ConflictError(
                'BATCH_JOB_NOT_TERMINAL',
                '완료되지 않은 배치 작업은 AI 재처리를 시작할 수 없습니다.',
            )
        if source.source_page_id is None:
            raise NotFoundError(
                'AI_RETRY_SOURCE_PAGE_NOT_FOUND',
                'AI 재처리에 사용할 기존 페이지를 찾을 수 없습니다.',
            )
        try:
            result = await enqueuer.enqueue(
                source=source,
                triggered_by_user_id=user_id,
                idempotency_key=idempotency_key,
            )
        except AiRetryIdempotencyConflictError as exc:
            raise ConflictError(
                'IDEMPOTENCY_KEY_REUSED',
                'Idempotency-Key가 다른 AI 재처리 요청에 이미 사용되었습니다.',
            ) from exc
        except IntegrityError as exc:
            raise ConflictError(
                'AI_RETRY_ALREADY_RUNNING',
                '동일 날짜의 AI 재처리 작업이 이미 실행 중입니다.',
            ) from exc

        if result.created:
            await self._repo.add_event(
                job_id=result.job.job_id,
                step_code='AI_RETRY_ENQUEUE',
                level='INFO',
                message='AI summary retry enqueued.',
                context_json={
                    'sourceJobId': source.source_job_id,
                    'sourcePageId': source.source_page_id,
                    'idempotencyKey': idempotency_key,
                },
            )
        await enqueuer.commit()
        return build_ai_retry_run_payload(result.job, created=result.created)


def _validate_idempotent_replay(
    job: object,
    *,
    business_date: date,
    run_mode: str,
    force: bool,
) -> None:
    if (
        getattr(job, 'business_date', None) != business_date
        or getattr(job, 'run_mode', None) != run_mode
        or bool(getattr(job, 'force_run', False)) != force
    ):
        raise ConflictError(
            'IDEMPOTENCY_KEY_REUSED',
            'Idempotency-Key가 다른 배치 요청에 이미 사용되었습니다.',
        )


def resolve_completed_news_slot(now: datetime) -> tuple[datetime, datetime]:
    if now.tzinfo is None:
        raise ValueError('News collection clock must be timezone-aware.')
    local_now = now.astimezone(KST)
    aligned_minute = 30 if local_now.minute >= 30 else 0
    window_end_at = local_now.replace(
        minute=aligned_minute,
        second=0,
        microsecond=0,
    )
    window_start_at = window_end_at - timedelta(minutes=30)
    return window_start_at, window_end_at


def resolve_news_collection_slot(
    *,
    now: datetime,
    requested_slot_end_at: datetime | None,
    max_backfill_days: int,
) -> tuple[datetime, datetime]:
    _, latest_completed_end_at = resolve_completed_news_slot(now)
    if requested_slot_end_at is None:
        window_end_at = latest_completed_end_at
    else:
        if (
            requested_slot_end_at.tzinfo is None
            or requested_slot_end_at.utcoffset() is None
        ):
            raise ConflictError(
                'NEWS_SLOT_INVALID',
                '뉴스 수집 슬롯 종료 시각에는 시간대 정보가 필요합니다.',
            )
        window_end_at = requested_slot_end_at.astimezone(KST)
        if (
            window_end_at.minute not in {0, 30}
            or window_end_at.second != 0
            or window_end_at.microsecond != 0
        ):
            raise ConflictError(
                'NEWS_SLOT_INVALID',
                '뉴스 수집 슬롯은 KST 기준 30분 경계에 맞아야 합니다.',
            )
        if window_end_at > latest_completed_end_at:
            raise ConflictError(
                'NEWS_SLOT_NOT_COMPLETED',
                '아직 완료되지 않은 뉴스 수집 슬롯입니다.',
            )
        earliest_end_at = latest_completed_end_at - timedelta(days=max_backfill_days)
        if window_end_at < earliest_end_at:
            raise ConflictError(
                'NEWS_SLOT_OUT_OF_RANGE',
                f'뉴스 수집 슬롯은 최근 {max_backfill_days}일 이내여야 합니다.',
            )
    return window_end_at - timedelta(minutes=30), window_end_at


def _build_news_collection_payload(
    job: BatchJobRecord,
    run: NewsCollectionRunRecord,
    created: bool,
) -> dict[str, object]:
    return {
        'jobId': job.job_id,
        'runId': run.run_id,
        'jobName': job.job_name,
        'status': job.status,
        'providerName': run.provider_name,
        'windowStartAt': run.window_start_at,
        'windowEndAt': run.window_end_at,
        'queryStartAt': run.query_start_at,
        'queryEndAt': run.query_end_at,
        'queuedAt': job.queued_at or job.started_at,
        '_created': created,
    }


__all__ = [
    'BatchesService',
]
