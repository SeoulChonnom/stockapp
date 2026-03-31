from __future__ import annotations

from app.batch.models import BatchExecutionContext
from app.batch.providers import NAVER_NEWS_PROVIDER_NAME, NaverNewsProvider
from app.batch.steps.base import BatchStep
from app.db.enums import EventLevel
from app.db.repositories.news_article_raw_repo import NewsArticleRawRepository
from app.db.repositories.news_search_keyword_repo import NewsSearchKeywordRepository
from app.db.repositories.batch_job_repo import BatchJobRepository


class CollectNewsStep(BatchStep):
    step_code = "COLLECT_NEWS"
    started_message = "Collect news step started."
    completed_message = "Collect news step completed."

    async def run(
        self,
        repository: BatchJobRepository,
        context: BatchExecutionContext,
    ) -> BatchExecutionContext:
        keyword_repo = NewsSearchKeywordRepository(repository.session)
        raw_repo = NewsArticleRawRepository(repository.session)
        provider = NaverNewsProvider()

        keywords = await keyword_repo.list_active_keywords(provider_name=NAVER_NEWS_PROVIDER_NAME)
        if not keywords:
            await repository.add_event(
                job_id=context.job_id,
                step_code=self.step_code,
                level=EventLevel.WARN.value,
                message="No active Naver news search keywords are configured.",
            )
            context.log_messages.append("No active Naver news keywords configured.")
            return context

        if not provider.is_configured():
            raise RuntimeError("Naver news API credentials are not configured.")

        total_fetched = 0
        total_candidates = 0
        total_inserted = 0

        for keyword in keywords:
            collection = await provider.collect_for_keyword(
                keyword_record=keyword,
                business_date=context.business_date,
            )
            inserted_count = await raw_repo.insert_articles(collection.articles)
            total_fetched += collection.fetched_count
            total_candidates += collection.candidate_count
            total_inserted += inserted_count
            await repository.add_event(
                job_id=context.job_id,
                step_code=self.step_code,
                level=EventLevel.INFO.value,
                message="Collected Naver news for keyword.",
                context_json={
                    "provider": keyword.provider_name,
                    "marketType": keyword.market_type,
                    "keyword": keyword.keyword,
                    "fetchedCount": collection.fetched_count,
                    "candidateCount": collection.candidate_count,
                    "insertedCount": inserted_count,
                },
            )

        context.raw_news_count += total_inserted
        context.log_messages.append(
            "Collected raw news from Naver "
            f"(fetched={total_fetched}, matched={total_candidates}, inserted={total_inserted})."
        )
        return context


__all__ = ["CollectNewsStep"]
