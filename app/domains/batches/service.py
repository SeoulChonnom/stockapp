from __future__ import annotations

from datetime import date

from sqlalchemy.exc import IntegrityError  # pyright: ignore[reportMissingImports]

from app.core.exceptions import ConflictError, NotFoundError
from app.core.timezone import get_business_date
from app.db.enums import BatchJobStatus, BatchRunMode, BatchTriggerType
from app.db.repositories.ai_retry_repo import (
    AiRetryEnqueuePort,
    AiRetryIdempotencyConflictError,
    PostgresAiRetryRepository,
)
from app.db.repositories.batch_job_repo import BatchJobRepository
from app.db.repositories.projections import BatchJobCreateParams
from app.domains.batches.assembler import (
    build_batch_job_detail_payload,
    build_batch_job_list_payload,
    build_batch_run_payload,
)


class BatchesService:
    def __init__(
        self,
        repository: BatchJobRepository,
        *,
        max_attempts: int = 3,
        ai_retry_enqueuer: AiRetryEnqueuePort | None = None,
    ) -> None:
        self._repo = repository
        self._max_attempts = max_attempts
        self._ai_retry_enqueuer = ai_retry_enqueuer

    async def list_jobs(
        self,
        *,
        from_date: date | None,
        to_date: date | None,
        status: str | None,
        page: int,
        size: int,
    ) -> dict[str, object]:
        result = await self._repo.list_jobs(
            from_date=from_date,
            to_date=to_date,
            status=status,
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
        return build_batch_job_detail_payload(job)

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
                _validate_idempotent_replay(
                    existing_job,
                    business_date=resolved_business_date,
                    run_mode=run_mode,
                    force=force,
                )
                return build_batch_run_payload(existing_job)

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
            raise ConflictError(
                'PAGE_ALREADY_EXISTS',
                '이미 생성된 페이지가 있어 배치를 시작할 수 없습니다.',
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
                        page_source.batch_job_id if page_source is not None else None
                    ),
                    source_page_id=(
                        page_source.page_id if page_source is not None else None
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
                    _validate_idempotent_replay(
                        existing_job,
                        business_date=resolved_business_date,
                        run_mode=run_mode,
                        force=force,
                    )
                    return build_batch_run_payload(existing_job)
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
        return build_batch_run_payload(job)

    async def retry_ai_summaries(
        self,
        *,
        requested_job_id: int,
        user_id: str | None,
        idempotency_key: str | None,
    ) -> dict[str, object]:
        enqueuer = self._ai_retry_enqueuer
        if enqueuer is None:
            enqueuer = PostgresAiRetryRepository(self._repo.session)
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
        job = result.job
        return {
            'jobId': job.job_id,
            'jobName': job.job_name,
            'businessDate': job.business_date.isoformat(),
            'status': job.status,
            'runMode': job.run_mode,
            'sourceJobId': job.source_job_id,
            'sourcePageId': job.source_page_id,
            'idempotencyKey': job.idempotency_key,
            'startedAt': job.started_at.isoformat(),
        }


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


__all__ = [
    'BatchesService',
]
