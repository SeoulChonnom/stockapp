from __future__ import annotations

from app.batch.models import BatchExecutionContext
from app.batch.providers.llm_provider import BatchLlmProvider, PROMPT_VERSION
from app.batch.steps.base import BatchStep
from app.db.enums import AiSummaryStatus, AiSummaryType, EventLevel
from app.db.repositories.batch_job_repo import BatchJobRepository
from app.db.repositories.ai_summary_write_repo import AiSummaryWriteRepository
from app.db.repositories.cluster_repo import ClusterRepository
from app.db.repositories.market_index_repo import MarketIndexRepository
from app.db.repositories.projections import AiSummaryCreateParams


class GenerateAiSummariesStep(BatchStep):
    step_code = "GENERATE_AI_SUMMARIES"
    started_message = "Generate AI summaries step started."
    completed_message = "Generate AI summaries step completed."

    async def run(
        self,
        repository: BatchJobRepository,
        context: BatchExecutionContext,
    ) -> BatchExecutionContext:
        session = getattr(repository, "session", None)
        if session is None or not hasattr(session, "bind"):
            context.log_messages.append("AI summary generation step is scaffolded.")
            return context

        cluster_repo = ClusterRepository(session)
        index_repo = MarketIndexRepository(session)
        summary_repo = AiSummaryWriteRepository(session)
        llm_provider = BatchLlmProvider()

        clusters = await cluster_repo.list_clusters_by_business_date(context.business_date)
        indices = await index_repo.list_indices_by_business_date(context.business_date)
        if not clusters and not context.rebuild_page_only:
            context.partial_reasons.append("요약 생성에 필요한 클러스터가 없습니다.")
            return context

        global_payload = await _generate_global_headline(llm_provider, clusters, indices)
        await summary_repo.insert_summary(
            AiSummaryCreateParams(
                batch_job_id=context.job_id,
                summary_type=AiSummaryType.GLOBAL_HEADLINE.value,
                business_date=context.business_date,
                market_type=None,
                cluster_id=None,
                title=global_payload["title"],
                body=global_payload.get("body"),
                paragraphs_json=[],
                model_name=global_payload.get("model_name"),
                prompt_version=PROMPT_VERSION,
                status=global_payload["status"],
                fallback_used=global_payload["fallback_used"],
                error_message=global_payload.get("error_message"),
                metadata_json=global_payload.get("metadata_json", {}),
            )
        )
        context.generated_summary_count += 1
        context.fallback_count += int(global_payload["fallback_used"])

        by_market: dict[str, list[dict]] = {}
        for cluster in clusters:
            by_market.setdefault(cluster["market_type"], []).append(cluster)
        indices_by_market: dict[str, list] = {}
        for index in indices:
            indices_by_market.setdefault(index.market_type, []).append(index)

        for market_type, market_clusters in by_market.items():
            market_summary = await _generate_market_summary(
                llm_provider,
                market_type=market_type,
                clusters=market_clusters,
                indices=indices_by_market.get(market_type, []),
            )
            await summary_repo.insert_summary(
                AiSummaryCreateParams(
                    batch_job_id=context.job_id,
                    summary_type=AiSummaryType.MARKET_SUMMARY.value,
                    business_date=context.business_date,
                    market_type=market_type,
                    cluster_id=None,
                    title=market_summary["title"],
                    body=market_summary.get("body"),
                    paragraphs_json=[],
                    model_name=market_summary.get("model_name"),
                    prompt_version=PROMPT_VERSION,
                    status=market_summary["status"],
                    fallback_used=market_summary["fallback_used"],
                    error_message=market_summary.get("error_message"),
                    metadata_json=market_summary.get("metadata_json", {}),
                )
            )
            context.generated_summary_count += 1
            context.fallback_count += int(market_summary["fallback_used"])

            for cluster in market_clusters:
                cluster_articles = await cluster_repo.get_cluster_articles(cluster["id"])
                processed_articles = await cluster_repo.get_processed_articles(
                    [row["processed_article_id"] for row in cluster_articles]
                )
                card_summary = await _generate_cluster_card_summary(
                    llm_provider,
                    market_type,
                    cluster,
                    processed_articles,
                )
                await summary_repo.insert_summary(
                    AiSummaryCreateParams(
                        batch_job_id=context.job_id,
                        summary_type=AiSummaryType.CLUSTER_CARD_SUMMARY.value,
                        business_date=context.business_date,
                        market_type=market_type,
                        cluster_id=cluster["id"],
                        title=card_summary.get("title"),
                        body=card_summary.get("body"),
                        paragraphs_json=[],
                        model_name=card_summary.get("model_name"),
                        prompt_version=PROMPT_VERSION,
                        status=card_summary["status"],
                        fallback_used=card_summary["fallback_used"],
                        error_message=card_summary.get("error_message"),
                        metadata_json=card_summary.get("metadata_json", {}),
                    )
                )
                detail_summary = await _generate_cluster_detail_summary(
                    llm_provider,
                    market_type,
                    cluster,
                    processed_articles,
                )
                await summary_repo.insert_summary(
                    AiSummaryCreateParams(
                        batch_job_id=context.job_id,
                        summary_type=AiSummaryType.CLUSTER_DETAIL_ANALYSIS.value,
                        business_date=context.business_date,
                        market_type=market_type,
                        cluster_id=cluster["id"],
                        title=detail_summary.get("title"),
                        body=detail_summary.get("body"),
                        paragraphs_json=detail_summary.get("paragraphs", []),
                        model_name=detail_summary.get("model_name"),
                        prompt_version=PROMPT_VERSION,
                        status=detail_summary["status"],
                        fallback_used=detail_summary["fallback_used"],
                        error_message=detail_summary.get("error_message"),
                        metadata_json=detail_summary.get("metadata_json", {}),
                    )
                )
                context.generated_summary_count += 2
                context.fallback_count += int(card_summary["fallback_used"]) + int(detail_summary["fallback_used"])

        await session.commit()
        if context.fallback_count:
            await repository.add_event(
                job_id=context.job_id,
                step_code=self.step_code,
                level=EventLevel.WARN.value,
                message="AI summaries generated with fallback responses.",
                context_json={"fallbackCount": context.fallback_count},
            )

        context.log_messages.append(f"Generated {context.generated_summary_count} AI summary row(s).")
        return context


async def _generate_global_headline(llm_provider: BatchLlmProvider, clusters: list[dict], indices: list) -> dict:
    fallback_title = "시장 주요 이슈를 종합한 글로벌 일간 요약"
    if clusters:
        fallback_title = " / ".join(cluster["title"] for cluster in clusters[:2])
    fallback = {
        "title": fallback_title,
        "body": None,
        "status": AiSummaryStatus.FALLBACK.value,
        "fallback_used": True,
        "metadata_json": {"reason": "llm_fallback"},
    }
    if not llm_provider.is_configured():
        return fallback
    try:
        result = await llm_provider.summarize_global_headline(
            clusters=[{"title": cluster["title"], "summary": cluster["summary_short"]} for cluster in clusters],
            indices=[
                {
                    "marketType": index.market_type,
                    "indexCode": index.index_code,
                    "changePercent": str(index.change_percent),
                }
                for index in indices
            ],
        )
        return {
            "title": result.get("title") or fallback_title,
            "body": result.get("body"),
            "status": AiSummaryStatus.SUCCESS.value,
            "fallback_used": False,
            "model_name": "gemini-3.1-flash-lite",
            "metadata_json": {"reason": "llm"},
        }
    except Exception as exc:
        fallback["error_message"] = str(exc)
        return fallback


async def _generate_market_summary(llm_provider: BatchLlmProvider, *, market_type: str, clusters: list[dict], indices: list) -> dict:
    fallback = {
        "title": f"{market_type} 시장 핵심 이슈 요약",
        "body": (clusters[0]["summary_short"] if clusters else None),
        "status": AiSummaryStatus.FALLBACK.value,
        "fallback_used": True,
        "metadata_json": {
            "background": [cluster["summary_short"] for cluster in clusters[:2] if cluster["summary_short"]],
            "keyThemes": [tag for cluster in clusters[:2] for tag in (cluster.get("tags_json") or [])][:5],
            "outlook": clusters[0]["summary_long"] if clusters else None,
        },
    }
    if not llm_provider.is_configured():
        return fallback
    try:
        result = await llm_provider.summarize_market(
            market_type=market_type,
            indices=[
                {
                    "indexCode": index.index_code,
                    "indexName": index.index_name,
                    "changePercent": str(index.change_percent),
                }
                for index in indices
            ],
            clusters=[
                {
                    "title": cluster["title"],
                    "summary": cluster["summary_short"],
                    "tags": cluster.get("tags_json") or [],
                }
                for cluster in clusters
            ],
        )
        return {
            "title": result.get("title") or fallback["title"],
            "body": result.get("body") or fallback["body"],
            "status": AiSummaryStatus.SUCCESS.value,
            "fallback_used": False,
            "model_name": "gemini-3.1-flash-lite",
            "metadata_json": {
                "background": result.get("background") or fallback["metadata_json"]["background"],
                "keyThemes": result.get("key_themes") or fallback["metadata_json"]["keyThemes"],
                "outlook": result.get("outlook") or fallback["metadata_json"]["outlook"],
            },
        }
    except Exception as exc:
        fallback["error_message"] = str(exc)
        return fallback


async def _generate_cluster_card_summary(
    llm_provider: BatchLlmProvider,
    market_type: str,
    cluster: dict,
    articles: list[dict],
) -> dict:
    fallback = {
        "title": cluster["title"],
        "body": cluster["summary_short"],
        "status": AiSummaryStatus.FALLBACK.value,
        "fallback_used": True,
        "metadata_json": {"reason": "llm_fallback"},
    }
    if not llm_provider.is_configured():
        return fallback
    try:
        result = await llm_provider.summarize_cluster_card(
            market_type=market_type,
            cluster={"title": cluster["title"], "summary": cluster["summary_short"]},
            articles=articles,
        )
        return {
            "title": result.get("title") or fallback["title"],
            "body": result.get("body") or fallback["body"],
            "status": AiSummaryStatus.SUCCESS.value,
            "fallback_used": False,
            "model_name": "gemini-3.1-flash-lite",
            "metadata_json": {"reason": "llm"},
        }
    except Exception as exc:
        fallback["error_message"] = str(exc)
        return fallback


async def _generate_cluster_detail_summary(
    llm_provider: BatchLlmProvider,
    market_type: str,
    cluster: dict,
    articles: list[dict],
) -> dict:
    fallback = {
        "title": cluster["title"],
        "body": cluster["summary_long"] or cluster["summary_short"],
        "paragraphs": cluster.get("analysis_paragraphs_json") or [],
        "status": AiSummaryStatus.FALLBACK.value,
        "fallback_used": True,
        "metadata_json": {"reason": "llm_fallback"},
    }
    if not llm_provider.is_configured():
        return fallback
    try:
        result = await llm_provider.summarize_cluster_detail(
            market_type=market_type,
            cluster={"title": cluster["title"], "summary": cluster["summary_long"] or cluster["summary_short"]},
            articles=articles,
        )
        return {
            "title": result.get("title") or fallback["title"],
            "body": result.get("body") or fallback["body"],
            "paragraphs": result.get("paragraphs") or fallback["paragraphs"],
            "status": AiSummaryStatus.SUCCESS.value,
            "fallback_used": False,
            "model_name": "gemini-3.1-flash-lite",
            "metadata_json": {"reason": "llm"},
        }
    except Exception as exc:
        fallback["error_message"] = str(exc)
        return fallback


__all__ = ["GenerateAiSummariesStep"]
