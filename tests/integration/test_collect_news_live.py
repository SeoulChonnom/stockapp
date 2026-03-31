from __future__ import annotations

import os
from datetime import datetime

import httpx
import pytest
from sqlalchemy import text

from app.batch.models import BatchExecutionContext
import app.batch.steps.collect_news as collect_news_module
from app.batch.providers.naver_news import NAVER_NEWS_PROVIDER_NAME, NaverNewsProvider
from app.batch.steps.collect_news import CollectNewsStep
from app.core.settings import get_settings
from app.core.timezone import KST
from app.db.enums import BatchJobStatus, BatchTriggerType
from app.db.repositories.batch_job_repo import BatchJobRepository
from app.db.repositories.news_search_keyword_repo import NewsSearchKeywordRepository
from app.db.repositories.projections import BatchJobCreateParams, NewsSearchKeywordCreateParams
from app.db.session import get_session_maker


pytestmark = pytest.mark.anyio


def _should_run_live_naver_test() -> bool:
    return os.getenv("RUN_NAVER_INTEGRATION_TEST") == "1"


async def _resolve_live_business_date(provider: NaverNewsProvider, query: str) -> datetime:
    async with httpx.AsyncClient(
        timeout=get_settings().naver_news_timeout_seconds,
        verify=False,
    ) as client:
        payload = await provider._fetch_page(client=client, query=query, start=1)

    for item in payload.get("items", []):
        published_at = provider._parse_pub_date(item.get("pubDate"))
        if published_at is not None:
            return published_at

    raise AssertionError("Naver news API returned no parsable items for the integration keyword.")


@pytest.mark.skipif(
    not _should_run_live_naver_test(),
    reason="Set RUN_NAVER_INTEGRATION_TEST=1 to run live Naver collection test.",
)
async def test_collect_news_step_collects_live_naver_articles(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings()
    if not settings.naver_client_id or not settings.naver_client_secret:
        pytest.skip("Naver API credentials are not configured.")

    provider = NaverNewsProvider(settings)
    monkeypatch.setattr(
        provider,
        "_build_client",
        lambda: httpx.AsyncClient(
            timeout=settings.naver_news_timeout_seconds,
            verify=False,
        ),
    )
    monkeypatch.setattr(collect_news_module, "NaverNewsProvider", lambda: provider)

    base_keyword = "증시"
    published_at = await _resolve_live_business_date(provider, base_keyword)
    business_date = published_at.astimezone(KST).date()

    session_maker = get_session_maker()
    async with session_maker() as session:
        batch_repo = BatchJobRepository(session)
        keyword_repo = NewsSearchKeywordRepository(session)

        test_keyword = base_keyword
        test_provider_name = NAVER_NEWS_PROVIDER_NAME

        stale_job_ids_result = await session.execute(
            text(
                """
                SELECT id
                FROM stock.batch_job
                WHERE business_date = :business_date
                  AND force_run = TRUE
                  AND rebuild_page_only = FALSE
                  AND status = 'RUNNING'::stock.batch_job_status_enum
                  AND raw_news_count = 0
                  AND processed_news_count = 0
                  AND cluster_count = 0
                  AND page_id IS NULL
                """
            ),
            {"business_date": business_date},
        )
        stale_job_ids = list(stale_job_ids_result.scalars().all())
        for stale_job_id in stale_job_ids:
            await session.execute(
                text("DELETE FROM stock.batch_job_event WHERE batch_job_id = :job_id"),
                {"job_id": stale_job_id},
            )
            await session.execute(
                text("DELETE FROM stock.batch_job WHERE id = :job_id"),
                {"job_id": stale_job_id},
            )

        await session.execute(
            text(
                """
                DELETE FROM stock.news_article_raw
                WHERE provider_name = :provider_name
                  AND search_keyword = :search_keyword
                  AND business_date = :business_date
                """
            ),
            {
                "provider_name": test_provider_name,
                "search_keyword": test_keyword,
                "business_date": business_date,
            },
        )
        await session.execute(
            text(
                """
                DELETE FROM stock.news_search_keyword
                WHERE provider_name = :provider_name
                  AND market_type = 'KR'::stock.market_type_enum
                  AND keyword = :keyword
                """
            ),
            {
                "provider_name": test_provider_name,
                "keyword": test_keyword,
            },
        )
        await session.commit()

        created_keyword = await keyword_repo.create_keyword(
            NewsSearchKeywordCreateParams(
                provider_name=test_provider_name,
                market_type="KR",
                keyword=test_keyword,
                priority=1,
                is_active=True,
            )
        )
        job = await batch_repo.create_job(
            BatchJobCreateParams(
                business_date=business_date,
                status=BatchJobStatus.RUNNING.value,
                trigger_type=BatchTriggerType.MANUAL.value,
                triggered_by_user_id=None,
                force_run=True,
                rebuild_page_only=False,
            )
        )

        try:
            step = CollectNewsStep()
            context = BatchExecutionContext(
                job_id=job.job_id,
                business_date=business_date,
                force_run=True,
                rebuild_page_only=False,
            )

            updated_context = await step.execute(batch_repo, context)

            raw_count_result = await session.execute(
                text(
                    """
                    SELECT count(*)
                    FROM stock.news_article_raw
                    WHERE provider_name = :provider_name
                      AND search_keyword = :search_keyword
                      AND business_date = :business_date
                    """
                ),
                {
                    "provider_name": test_provider_name,
                    "search_keyword": test_keyword,
                    "business_date": business_date,
                },
            )
            event_count_result = await session.execute(
                text(
                    """
                    SELECT count(*)
                    FROM stock.batch_job_event
                    WHERE batch_job_id = :job_id
                      AND step_code = 'COLLECT_NEWS'
                    """
                ),
                {"job_id": job.job_id},
            )

            inserted_raw_count = raw_count_result.scalar_one()
            event_count = event_count_result.scalar_one()

            assert updated_context.raw_news_count > 0
            assert inserted_raw_count > 0
            assert event_count >= 3
        finally:
            await session.rollback()
            await session.execute(
                text(
                    """
                    DELETE FROM stock.news_article_raw
                    WHERE provider_name = :provider_name
                      AND search_keyword = :search_keyword
                      AND business_date = :business_date
                    """
                ),
                {
                    "provider_name": test_provider_name,
                    "search_keyword": test_keyword,
                    "business_date": business_date,
                },
            )
            await session.execute(
                text("DELETE FROM stock.batch_job_event WHERE batch_job_id = :job_id"),
                {"job_id": job.job_id},
            )
            await session.execute(
                text("DELETE FROM stock.batch_job WHERE id = :job_id"),
                {"job_id": job.job_id},
            )
            await session.execute(
                text("DELETE FROM stock.news_search_keyword WHERE id = :keyword_id"),
                {"keyword_id": created_keyword.keyword_id},
            )
            await session.commit()
