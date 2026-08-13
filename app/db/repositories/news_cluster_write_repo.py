from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import date

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.identifiers import qualify_db_identifier
from app.db.repositories.base import PostgresRepository
from app.db.repositories.projections import (
    NewsClusterArticleCreateParams,
    NewsClusterCreateParams,
    NewsClusterWriteRecord,
    ThemeAssignmentCreateParams,
)
from app.db.repositories.theme_repo import ThemeRepository


class NewsClusterWriteRepository(PostgresRepository):
    def __init__(
        self,
        session: AsyncSession,
        *,
        theme_repository: ThemeRepository | None = None,
    ) -> None:
        super().__init__(session)
        self._theme_repository = theme_repository or ThemeRepository(session)

    async def list_cluster_ids_for_business_date(
        self,
        business_date: date,
        market_type: str,
        *,
        min_rank: int | None = None,
    ) -> list[int]:
        min_rank_filter = 'AND cluster_rank > :min_rank' if min_rank is not None else ''
        statement = text(
            """
            SELECT id
            FROM {cluster_table}
            WHERE business_date = :business_date
              AND market_type = CAST(:market_type AS {market_type_enum})
              {min_rank_filter}
            ORDER BY cluster_rank ASC
            """.format(
                cluster_table=qualify_db_identifier('news_cluster'),
                market_type_enum=qualify_db_identifier('market_type_enum'),
                min_rank_filter=min_rank_filter,
            )
        )
        params: dict[str, object] = {
            'business_date': business_date,
            'market_type': market_type,
        }
        if min_rank is not None:
            params['min_rank'] = min_rank
        result = await self.session.execute(statement, params)
        return list(result.scalars().all())

    async def delete_clusters_by_ids(self, cluster_ids: list[int]) -> None:
        if not cluster_ids:
            return
        statement = text(
            """
            DELETE FROM {cluster_table}
            WHERE id IN :cluster_ids
            """.format(cluster_table=qualify_db_identifier('news_cluster'))
        ).bindparams(bindparam('cluster_ids', expanding=True))
        await self.session.execute(statement, {'cluster_ids': tuple(cluster_ids)})

    async def upsert_cluster(
        self, params: NewsClusterCreateParams
    ) -> NewsClusterWriteRecord:
        statement = text(
            """
            INSERT INTO {cluster_table} (
                business_date,
                market_type,
                cluster_rank,
                title,
                summary_short,
                summary_long,
                analysis_paragraphs_json,
                tags_json,
                representative_article_id,
                article_count
            )
            VALUES (
                :business_date,
                CAST(:market_type AS {market_type_enum}),
                :cluster_rank,
                :title,
                :summary_short,
                :summary_long,
                CAST(:analysis_paragraphs_json AS JSONB),
                CAST(:tags_json AS JSONB),
                :representative_article_id,
                :article_count
            )
            ON CONFLICT (business_date, market_type, cluster_rank) DO UPDATE
            SET
                title = EXCLUDED.title,
                summary_short = EXCLUDED.summary_short,
                summary_long = EXCLUDED.summary_long,
                analysis_paragraphs_json = EXCLUDED.analysis_paragraphs_json,
                tags_json = EXCLUDED.tags_json,
                representative_article_id = EXCLUDED.representative_article_id,
                article_count = EXCLUDED.article_count,
                updated_at = now()
            RETURNING
                id AS cluster_id,
                cluster_uid,
                cluster_rank
            """.format(
                cluster_table=qualify_db_identifier('news_cluster'),
                market_type_enum=qualify_db_identifier('market_type_enum'),
            )
        )
        result = await self.session.execute(
            statement,
            {
                'business_date': params.business_date,
                'market_type': params.market_type,
                'cluster_rank': params.cluster_rank,
                'title': params.title,
                'summary_short': params.summary_short,
                'summary_long': params.summary_long,
                'analysis_paragraphs_json': json.dumps(params.analysis_paragraphs_json),
                'tags_json': json.dumps(params.tags_json),
                'representative_article_id': params.representative_article_id,
                'article_count': params.article_count,
            },
        )
        row = result.mappings().one()
        return self._model_from_mapping(NewsClusterWriteRecord, row)

    async def replace_cluster_articles(
        self,
        cluster_id: int,
        memberships: list[NewsClusterArticleCreateParams],
    ) -> None:
        delete_statement = text(
            """
            DELETE FROM {cluster_article_table}
            WHERE cluster_id = :cluster_id
            """.format(
                cluster_article_table=qualify_db_identifier('news_cluster_article')
            )
        )
        await self.session.execute(delete_statement, {'cluster_id': cluster_id})

        if memberships:
            insert_statement = text(
                """
                INSERT INTO {cluster_article_table} (
                    cluster_id,
                    processed_article_id,
                    article_rank
                )
                VALUES (
                    :cluster_id,
                    :processed_article_id,
                    :article_rank
                )
                """.format(
                    cluster_article_table=qualify_db_identifier('news_cluster_article')
                )
            )
            for membership in memberships:
                await self.session.execute(
                    insert_statement,
                    {
                        'cluster_id': cluster_id,
                        'processed_article_id': membership.processed_article_id,
                        'article_rank': membership.article_rank,
                    },
                )

    async def replace_cluster_themes(
        self,
        cluster_id: int,
        assignments: Sequence[object],
    ) -> None:
        """Replace ranked themes in the caller's current transaction.

        An empty assignment sequence intentionally clears previous themes when
        both LLM and deterministic fallback classification produce no result.
        This method never commits so cluster and theme writes can remain
        atomic in the enclosing batch-step transaction.
        """
        normalized_assignments = self._validate_theme_assignments(assignments)
        if normalized_assignments:
            invalid_codes = (
                await self._theme_repository.validate_active_leaf_theme_codes(
                    [assignment.theme_code for assignment in normalized_assignments]
                )
            )
            if invalid_codes:
                invalid_display = ', '.join(repr(code) for code in invalid_codes)
                raise ValueError(
                    f'theme assignments must use active leaf codes: {invalid_display}'
                )

        delete_statement = text(
            """
            DELETE FROM {cluster_theme_table}
            WHERE cluster_id = :cluster_id
            """.format(cluster_theme_table=qualify_db_identifier('news_cluster_theme'))
        )
        await self.session.execute(delete_statement, {'cluster_id': cluster_id})

        if not normalized_assignments:
            return

        insert_statement = text(
            """
            INSERT INTO {cluster_theme_table} (
                cluster_id,
                theme_code,
                rank,
                classification_method
            )
            VALUES (
                :cluster_id,
                :theme_code,
                :rank,
                :classification_method
            )
            """.format(cluster_theme_table=qualify_db_identifier('news_cluster_theme'))
        )
        await self.session.execute(
            insert_statement,
            [
                {
                    'cluster_id': cluster_id,
                    'theme_code': assignment.theme_code,
                    'rank': assignment.rank,
                    'classification_method': assignment.classification_method,
                }
                for assignment in normalized_assignments
            ],
        )

    @staticmethod
    def _validate_theme_assignments(
        assignments: Sequence[object],
    ) -> list[ThemeAssignmentCreateParams]:
        try:
            raw_assignments = list(assignments)
        except TypeError as exc:
            raise ValueError('theme assignments must be a sequence') from exc

        if not raw_assignments:
            return []
        if len(raw_assignments) > 3:
            raise ValueError('theme assignments must contain between 1 and 3 items')

        normalized: list[ThemeAssignmentCreateParams] = []
        for assignment in raw_assignments:
            theme_code = getattr(assignment, 'theme_code', None)
            rank = getattr(assignment, 'rank', None)
            classification_method = getattr(
                assignment,
                'classification_method',
                None,
            )
            if not isinstance(theme_code, str) or not theme_code:
                raise ValueError('theme assignment code must be a nonempty string')
            if isinstance(rank, bool) or not isinstance(rank, int):
                raise ValueError('theme assignment rank must be an integer')
            if classification_method not in {'LLM', 'KEYWORD_FALLBACK'}:
                raise ValueError(
                    'theme assignment classification_method must be LLM or '
                    'KEYWORD_FALLBACK'
                )
            normalized.append(
                ThemeAssignmentCreateParams(
                    theme_code=theme_code,
                    rank=rank,
                    classification_method=classification_method,
                )
            )

        codes = [assignment.theme_code for assignment in normalized]
        if len(codes) != len(set(codes)):
            raise ValueError('theme assignment codes must be unique')
        ranks = sorted(assignment.rank for assignment in normalized)
        if ranks != list(range(1, len(normalized) + 1)):
            raise ValueError('theme assignment ranks must be contiguous from 1')
        return sorted(normalized, key=lambda assignment: assignment.rank)

    async def create_cluster_bundle(
        self,
        params: NewsClusterCreateParams,
        article_ids: list[int],
    ) -> NewsClusterWriteRecord:
        cluster = await self.upsert_cluster(params)
        await self.replace_cluster_articles(
            cluster.cluster_id,
            [
                NewsClusterArticleCreateParams(
                    cluster_id=cluster.cluster_id,
                    processed_article_id=processed_article_id,
                    article_rank=index,
                )
                for index, processed_article_id in enumerate(article_ids, start=1)
            ],
        )
        return cluster


__all__ = ['NewsClusterWriteRepository']
