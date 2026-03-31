from __future__ import annotations

import pytest

pytest.importorskip("sqlalchemy")

from tests.support import DummyResult, RecordingAsyncSession, jsonable, load_module, normalize_sql

cluster_write_repo_module = load_module("app.db.repositories.news_cluster_write_repo")
projections_module = load_module("app.db.repositories.projections")

NewsClusterWriteRepository = cluster_write_repo_module.NewsClusterWriteRepository
NewsClusterCreateParams = projections_module.NewsClusterCreateParams


@pytest.mark.anyio
async def test_create_cluster_bundle_inserts_cluster_and_memberships():
    session = RecordingAsyncSession(
        results=[
            DummyResult(
                [
                    {
                        "cluster_id": 7001,
                        "cluster_uid": "51f0d9a0-9fc5-4f15-a4f9-62856f128683",
                        "business_date": "2026-03-17",
                        "market_type": "US",
                        "cluster_rank": 1,
                        "title": "엔비디아 및 반도체 강세에 기술주 상승",
                        "summary_short": "반도체 업종 강세가 나스닥 상승을 견인했다.",
                        "summary_long": "PPI 둔화 신호와 장기 금리 하락이 나스닥 중심 랠리를 자극했다.",
                        "analysis_paragraphs_json": [],
                        "tags_json": [],
                        "representative_article_id": 4001,
                        "article_count": 2,
                        "created_at": "2026-03-18T06:12:10+00:00",
                        "updated_at": "2026-03-18T06:12:10+00:00",
                    }
                ]
            )
        ]
    )
    repo = NewsClusterWriteRepository(session)

    result = await repo.create_cluster_bundle(
        NewsClusterCreateParams(
            business_date="2026-03-17",
            market_type="US",
            cluster_rank=1,
            title="엔비디아 및 반도체 강세에 기술주 상승",
            summary_short="반도체 업종 강세가 나스닥 상승을 견인했다.",
            summary_long="PPI 둔화 신호와 장기 금리 하락이 나스닥 중심 랠리를 자극했다.",
            analysis_paragraphs_json=[],
            tags_json=[],
            representative_article_id=4001,
            article_count=2,
        ),
        [4001, 4002],
    )

    assert jsonable(result)["cluster_id"] == 7001
    sql = normalize_sql(session.statements[0])
    assert "news_cluster" in sql
    assert "representative_article_id" in sql
    assert "analysis_paragraphs_json" in sql
