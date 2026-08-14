from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from numbers import Real
from typing import Any, Literal, cast

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.batch.article_similarity import (
    SimilarityGroup,
    SimilarityGroupingResult,
    SimilarityGroupMember,
)
from app.db.identifiers import qualify_db_identifier
from app.db.repositories.base import PostgresRepository
from app.db.repositories.projections import (
    ArticleGroupingRecord,
    ArticleGroupMemberRecord,
    ArticleGroupRecord,
    ExactDuplicateCountRecord,
)

_GROUPING_FAILED = 'SIMILARITY_GROUPING_FAILED'


class ArticleGroupRepository(PostgresRepository):
    """Read and write persisted article similarity groups."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        super().__init__(session)
        self._now = now or (lambda: datetime.now(UTC))

    async def get_exact_duplicate_counts(
        self, processed_article_ids: Sequence[int]
    ) -> list[ExactDuplicateCountRecord]:
        """Count raw aliases for each canonical processed article.

        A processed-to-processed similarity relationship is deliberately absent
        from this query: only raw article mappings represent exact duplicates.
        """
        ids = self._validate_ids(processed_article_ids)
        if not ids:
            return []
        statement = text(
            """
            SELECT
                requested.processed_article_id,
                GREATEST(COUNT(DISTINCT raw_article_id) - 1, 0)
                    AS exact_duplicate_count
            FROM unnest(CAST(:processed_article_ids AS BIGINT[]))
                AS requested(processed_article_id)
            LEFT JOIN {mapping_table} mapping
              ON mapping.processed_article_id = requested.processed_article_id
            GROUP BY requested.processed_article_id
            ORDER BY requested.processed_article_id ASC
            """.format(
                mapping_table=qualify_db_identifier('news_article_raw_processed_map')
            )
        )
        result = await self.session.execute(statement, {'processed_article_ids': ids})
        return [
            self._record_from_mapping(ExactDuplicateCountRecord, row)
            for row in result.mappings().all()
        ]

    async def list_exact_duplicate_counts(
        self, processed_article_ids: Sequence[int]
    ) -> list[ExactDuplicateCountRecord]:
        """Compatibility alias for the exact-count read."""
        return await self.get_exact_duplicate_counts(processed_article_ids)

    async def get_exact_duplicate_counts_by_article_ids(
        self, processed_article_ids: Sequence[int]
    ) -> list[ExactDuplicateCountRecord]:
        """Compatibility alias used by cluster batch callers."""
        return await self.get_exact_duplicate_counts(processed_article_ids)

    async def replace_cluster_groups(
        self,
        cluster_id: int,
        result: SimilarityGroupingResult,
        *,
        algorithm_version: str | None = None,
        generated_at: datetime | None = None,
        exact_counts: Mapping[int, int] | Sequence[object] | None = None,
    ) -> None:
        """Atomically replace all groups and mark the source cluster READY.

        The caller owns the surrounding transaction.  This method intentionally
        never commits; failures roll back the shared session and are re-raised.
        """
        try:
            current_ids = await self._lock_cluster_and_article_ids(cluster_id)
            resolved_generated_at = generated_at or self._now()
            if exact_counts is None:
                raise ValueError('exact duplicate counts are required')
            counts = self._normalize_exact_counts(exact_counts)
            self._validate_exact_counts(counts, current_ids)
            await self._replace_cluster_groups(
                cluster_id,
                result,
                current_ids=current_ids,
                algorithm_version=algorithm_version,
                generated_at=resolved_generated_at,
                status='READY',
                issue_code=None,
                public_generated_at=resolved_generated_at,
                exact_counts=counts,
            )
        except Exception:
            await self.session.rollback()
            raise

    async def mark_grouping_unavailable_with_singletons(
        self,
        cluster_id: int,
        articles: Sequence[object],
        exact_counts: Mapping[int, int] | Sequence[object],
        algorithm_version: str,
    ) -> None:
        """Persist deterministic singleton groups for a failed cluster run."""
        try:
            current_ids = await self._lock_cluster_and_article_ids(cluster_id)
            article_ids = [self._article_id(article) for article in articles]
            if set(article_ids) != set(current_ids) or len(article_ids) != len(
                set(article_ids)
            ):
                raise ValueError(
                    'singleton article IDs must exactly match current cluster members'
                )
            ordered_ids = current_ids
            counts = self._normalize_exact_counts(exact_counts)
            self._validate_exact_counts(counts, current_ids)
            groups = tuple(
                SimilarityGroup(
                    group_rank=rank,
                    representative_article_id=article_id,
                    members=(
                        SimilarityGroupMember(
                            processed_article_id=article_id,
                            similarity_score=1.0,
                            is_representative=True,
                            article_rank=1,
                        ),
                    ),
                )
                for rank, article_id in enumerate(ordered_ids, start=1)
            )
            singleton_result = SimilarityGroupingResult(groups=groups)
            await self._replace_cluster_groups(
                cluster_id,
                singleton_result,
                current_ids=current_ids,
                algorithm_version=algorithm_version,
                generated_at=self._now(),
                status='UNAVAILABLE',
                issue_code=_GROUPING_FAILED,
                public_generated_at=None,
                exact_counts=counts,
            )
        except Exception:
            await self.session.rollback()
            raise

    async def get_cluster_grouping(self, cluster_id: int) -> ArticleGroupingRecord:
        """Read stored status and ranked memberships without recomputation."""
        status_statement = text(
            """
            SELECT article_grouping_status AS status,
                   article_grouping_generated_at,
                   article_grouping_issue_code
            FROM {cluster_table}
            WHERE id = :cluster_id
            """.format(cluster_table=qualify_db_identifier('news_cluster'))
        )
        groups_statement = text(
            """
            SELECT id AS similar_group_id, cluster_id, group_rank,
                   representative_article_id, algorithm_version, generated_at
            FROM {group_table}
            WHERE cluster_id = :cluster_id
            ORDER BY group_rank ASC
            """.format(group_table=qualify_db_identifier('news_cluster_similar_group'))
        )
        members_statement = text(
            """
            SELECT member.similar_group_id, member.processed_article_id,
                   member.similarity_score, member.exact_duplicate_count,
                   member.is_representative, member.article_rank
            FROM {member_table} member
            JOIN {group_table} header ON header.id = member.similar_group_id
            WHERE header.cluster_id = :cluster_id
            ORDER BY header.group_rank ASC, member.article_rank ASC,
                     member.processed_article_id ASC
            """.format(
                member_table=qualify_db_identifier(
                    'news_cluster_similar_group_article'
                ),
                group_table=qualify_db_identifier('news_cluster_similar_group'),
            )
        )
        status_result = await self.session.execute(
            status_statement, {'cluster_id': cluster_id}
        )
        status_row = status_result.mappings().one_or_none()
        if status_row is None:
            raise ValueError(f'cluster {cluster_id} was not found')
        groups_result = await self.session.execute(
            groups_statement, {'cluster_id': cluster_id}
        )
        members_result = await self.session.execute(
            members_statement, {'cluster_id': cluster_id}
        )
        groups = tuple(
            self._record_from_mapping(ArticleGroupRecord, row)
            for row in groups_result.mappings().all()
        )
        members = tuple(
            self._record_from_mapping(ArticleGroupMemberRecord, row)
            for row in members_result.mappings().all()
        )
        members_by_group: dict[int, list[ArticleGroupMemberRecord]] = {}
        for member in members:
            members_by_group.setdefault(member.similar_group_id, []).append(member)
        groups = tuple(
            ArticleGroupRecord(
                similar_group_id=group.similar_group_id,
                cluster_id=group.cluster_id,
                group_rank=group.group_rank,
                representative_article_id=group.representative_article_id,
                algorithm_version=group.algorithm_version,
                generated_at=group.generated_at,
                members=tuple(members_by_group.get(group.similar_group_id, ())),
            )
            for group in groups
        )
        versions = {group.algorithm_version for group in groups}
        if len(versions) > 1:
            raise ValueError(f'cluster {cluster_id} has mixed grouping versions')
        status = cast(Literal['READY', 'UNAVAILABLE'], str(status_row['status']))
        if status not in {'READY', 'UNAVAILABLE'}:
            raise ValueError(f'cluster {cluster_id} has invalid grouping status')
        return ArticleGroupingRecord(
            status=status,
            generated_at=status_row.get('article_grouping_generated_at'),
            issue_code=status_row.get('article_grouping_issue_code'),
            algorithm_version=next(iter(versions), None),
            groups=groups,
            members=members,
        )

    async def list_cluster_groups(self, cluster_id: int) -> list[ArticleGroupRecord]:
        """Return persisted group headers in rank order."""
        grouping = await self.get_cluster_grouping(cluster_id)
        return list(grouping.groups)

    async def get_grouping(self, cluster_id: int) -> ArticleGroupingRecord:
        """Compatibility alias for the persisted grouping read."""
        return await self.get_cluster_grouping(cluster_id)

    async def get_cluster_groups(self, cluster_id: int) -> list[ArticleGroupRecord]:
        """Return persisted group headers for one cluster."""
        return await self.list_cluster_groups(cluster_id)

    async def _replace_cluster_groups(
        self,
        cluster_id: int,
        result: SimilarityGroupingResult,
        *,
        current_ids: Sequence[int],
        algorithm_version: str | None,
        generated_at: datetime | None,
        status: str,
        issue_code: str | None,
        public_generated_at: datetime | None,
        exact_counts: Mapping[int, int] | None = None,
    ) -> None:
        groups = tuple(result.groups)
        if not isinstance(algorithm_version, str):
            raise ValueError('algorithm_version must be a non-empty string')
        normalized_version = algorithm_version.strip()
        if not normalized_version:
            raise ValueError('algorithm_version must be a non-empty string')
        internal_generated_at = generated_at or self._now()
        self._validate_grouping(groups, current_ids)
        if exact_counts is None:
            raise ValueError('exact duplicate counts are required')
        counts = dict(exact_counts)
        delete_statement = text(
            'DELETE FROM {group_table} WHERE cluster_id = :cluster_id'.format(
                group_table=qualify_db_identifier('news_cluster_similar_group')
            )
        )
        await self.session.execute(delete_statement, {'cluster_id': cluster_id})

        group_statement = text(
            """
            INSERT INTO {group_table} (
                cluster_id, group_rank, representative_article_id,
                algorithm_version, generated_at
            ) VALUES (
                :cluster_id, :group_rank, :representative_article_id,
                :algorithm_version, :generated_at
            )
            RETURNING id, group_rank
            """.format(group_table=qualify_db_identifier('news_cluster_similar_group'))
        )
        group_params = [
            {
                'cluster_id': cluster_id,
                'group_rank': group.group_rank,
                'representative_article_id': group.representative_article_id,
                'algorithm_version': normalized_version,
                'generated_at': internal_generated_at,
            }
            for group in groups
        ]
        group_ids: dict[int, int] = {}
        persisted_ids: set[int] = set()
        for group, params in zip(groups, group_params, strict=True):
            group_result = await self.session.execute(group_statement, params)
            row = group_result.mappings().one_or_none()
            if row is None or row.get('id') is None:
                raise RuntimeError('database did not return a group id')
            persisted_id = row['id']
            if isinstance(persisted_id, bool) or not isinstance(persisted_id, int):
                raise RuntimeError('database returned an invalid group id')
            returned_rank = row.get('group_rank', group.group_rank)
            if returned_rank != group.group_rank:
                raise RuntimeError('database returned an unexpected group rank')
            if persisted_id in persisted_ids:
                raise RuntimeError('database returned duplicate group ids')
            persisted_ids.add(persisted_id)
            group_ids[group.group_rank] = persisted_id

        member_statement = text(
            """
            INSERT INTO {member_table} (
                similar_group_id, processed_article_id, similarity_score,
                exact_duplicate_count, is_representative, article_rank
            ) VALUES (
                :similar_group_id, :processed_article_id, :similarity_score,
                :exact_duplicate_count, :is_representative, :article_rank
            )
            """.format(
                member_table=qualify_db_identifier('news_cluster_similar_group_article')
            )
        )
        member_params = [
            {
                'similar_group_id': group_ids[group.group_rank],
                'processed_article_id': member.processed_article_id,
                'similarity_score': member.similarity_score,
                'exact_duplicate_count': counts[member.processed_article_id],
                'is_representative': member.is_representative,
                'article_rank': member.article_rank,
            }
            for group in groups
            for member in group.members
        ]
        if member_params:
            await self.session.execute(member_statement, member_params)

        status_sql = 'READY' if status == 'READY' else 'UNAVAILABLE'
        if status_sql == 'UNAVAILABLE' and issue_code != _GROUPING_FAILED:
            raise ValueError('unavailable grouping requires the fixed issue code')
        update_statement = text(
            f"""
            UPDATE {qualify_db_identifier('news_cluster')}
            SET article_grouping_status = '{status_sql}',
                article_grouping_generated_at = :generated_at,
                article_grouping_issue_code = :issue_code
            WHERE id = :cluster_id
            """
        )
        await self.session.execute(
            update_statement,
            {
                'cluster_id': cluster_id,
                'generated_at': public_generated_at,
                'issue_code': issue_code,
            },
        )

    async def _lock_cluster_and_article_ids(self, cluster_id: int) -> list[int]:
        cluster_statement = text(
            """
            SELECT id
            FROM {cluster_table}
            WHERE id = :cluster_id
            FOR UPDATE
            """.format(cluster_table=qualify_db_identifier('news_cluster'))
        )
        cluster_result = await self.session.execute(
            cluster_statement, {'cluster_id': cluster_id}
        )
        if cluster_result.mappings().one_or_none() is None:
            raise ValueError(f'cluster {cluster_id} was not found')
        statement = text(
            """
            SELECT processed_article_id
            FROM {cluster_article_table}
            WHERE cluster_id = :cluster_id
            ORDER BY article_rank ASC, processed_article_id ASC
            FOR UPDATE
            """.format(
                cluster_article_table=qualify_db_identifier('news_cluster_article')
            )
        )
        result = await self.session.execute(statement, {'cluster_id': cluster_id})
        return [int(row['processed_article_id']) for row in result.mappings().all()]

    @staticmethod
    def _validate_grouping(
        groups: Sequence[SimilarityGroup], current_ids: Sequence[int]
    ) -> None:
        expected = set(current_ids)
        members: list[int] = []
        for expected_rank, group in enumerate(groups, start=1):
            if not isinstance(group.group_rank, int) or isinstance(
                group.group_rank, bool
            ):
                raise ValueError('group ranks must be positive integers')
            if group.group_rank != expected_rank:
                raise ValueError('group ranks must be contiguous from 1')
            if not group.members:
                raise ValueError('groups must contain at least one member')
            if not isinstance(group.representative_article_id, int) or isinstance(
                group.representative_article_id, bool
            ):
                raise ValueError('representative article IDs must be integers')
            representatives = [
                member.processed_article_id
                for member in group.members
                if member.is_representative
            ]
            if representatives != [group.representative_article_id]:
                raise ValueError('each group must have exactly one representative')
            for article_rank, member in enumerate(group.members, start=1):
                if member.article_rank != article_rank:
                    raise ValueError('member ranks must be contiguous from 1')
                if not isinstance(member.processed_article_id, int) or isinstance(
                    member.processed_article_id, bool
                ):
                    raise ValueError('processed article IDs must be integers')
                if not isinstance(member.article_rank, int) or isinstance(
                    member.article_rank, bool
                ):
                    raise ValueError('member ranks must be positive integers')
                if not isinstance(member.is_representative, bool):
                    raise ValueError('representative flags must be booleans')
                score = member.similarity_score
                if isinstance(score, bool) or not isinstance(score, Real):
                    raise ValueError(
                        'similarity score must be finite and between 0 and 1'
                    )
                try:
                    numeric_score = float(score)
                except (OverflowError, TypeError, ValueError) as exc:
                    raise ValueError(
                        'similarity score must be finite and between 0 and 1'
                    ) from exc
                if not math.isfinite(numeric_score) or not 0.0 <= numeric_score <= 1.0:
                    raise ValueError(
                        'similarity score must be finite and between 0 and 1'
                    )
                members.append(member.processed_article_id)
        if len(members) != len(set(members)) or set(members) != expected:
            raise ValueError(
                'group member IDs must exactly match current cluster members'
            )

    @staticmethod
    def _normalize_exact_counts(
        exact_counts: Mapping[int, int] | Sequence[object],
    ) -> dict[int, int]:
        if isinstance(exact_counts, Mapping):
            values = list(exact_counts.items())
        else:
            normalized_values: list[tuple[int, int]] = []
            seen_ids: set[int] = set()
            for item in exact_counts:
                if isinstance(item, Mapping):
                    try:
                        article_id = item['processed_article_id']
                        count = item['exact_duplicate_count']
                    except (KeyError, TypeError) as exc:
                        raise ValueError(
                            'exact duplicate count row is incomplete'
                        ) from exc
                else:
                    article_id = getattr(item, 'processed_article_id', None)
                    count = getattr(item, 'exact_duplicate_count', None)
                if article_id is None or count is None:
                    raise ValueError('exact duplicate count row is incomplete')
                if isinstance(article_id, bool) or not isinstance(article_id, int):
                    raise ValueError('exact duplicate counts require integer IDs')
                if article_id in seen_ids:
                    raise ValueError('exact duplicate counts contain duplicate IDs')
                seen_ids.add(article_id)
                normalized_values.append((article_id, count))
            values = normalized_values
        normalized: dict[int, int] = {}
        seen_ids: set[int] = set()
        for article_id, count in values:
            if isinstance(article_id, bool) or not isinstance(article_id, int):
                raise ValueError('exact duplicate counts require integer IDs')
            if article_id in seen_ids:
                raise ValueError('exact duplicate counts contain duplicate IDs')
            seen_ids.add(article_id)
            if isinstance(count, bool) or not isinstance(count, int):
                raise ValueError('exact duplicate counts require integer values')
            if count < 0:
                raise ValueError('exact duplicate counts must be non-negative')
            normalized[article_id] = count
        return normalized

    @staticmethod
    def _validate_exact_counts(
        counts: Mapping[int, int], current_ids: Sequence[int]
    ) -> None:
        if set(counts) != set(current_ids):
            raise ValueError(
                'exact duplicate counts must exactly match cluster members'
            )

    @staticmethod
    def _validate_ids(ids: Sequence[int]) -> list[int]:
        result = [int(value) for value in ids]
        if any(value <= 0 for value in result) or len(result) != len(set(result)):
            raise ValueError('processed article IDs must be unique positive integers')
        return result

    @staticmethod
    def _article_id(article: object) -> int:
        if isinstance(article, Mapping):
            value = article.get('processed_article_id', article.get('id'))
        else:
            value = getattr(
                article, 'processed_article_id', getattr(article, 'id', None)
            )
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError('article is missing a processed article ID')
        return value

    @staticmethod
    def _record_from_mapping(model: type[Any], row: Mapping[Any, Any]) -> Any:
        return model(**dict(row))


__all__ = ['ArticleGroupRepository']
