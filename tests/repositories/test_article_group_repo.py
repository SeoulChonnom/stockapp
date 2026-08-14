from __future__ import annotations

from dataclasses import FrozenInstanceError, is_dataclass
from datetime import UTC, datetime
from math import inf, nan

import pytest

from app.batch.article_similarity import (
    SimilarityGroup,
    SimilarityGroupingResult,
    SimilarityGroupMember,
)
from app.db.repositories.article_group_repo import ArticleGroupRepository
from app.db.repositories.projections import (
    ArticleGroupMemberRecord,
    ArticleGroupRecord,
    ExactDuplicateCountRecord,
)
from tests.support import DummyResult, RecordingAsyncSession, normalize_sql

GENERATED_AT = datetime(2026, 3, 18, 6, 12, tzinfo=UTC)


def _result(*groups: SimilarityGroup) -> SimilarityGroupingResult:
    return SimilarityGroupingResult(groups=tuple(groups))


def _group(
    rank: int,
    representative_id: int,
    *article_ids: int,
) -> SimilarityGroup:
    return SimilarityGroup(
        group_rank=rank,
        representative_article_id=representative_id,
        members=tuple(
            SimilarityGroupMember(
                processed_article_id=article_id,
                similarity_score=1.0,
                is_representative=article_id == representative_id,
                article_rank=index,
            )
            for index, article_id in enumerate(article_ids, start=1)
        ),
    )


def test_group_read_and_write_projections_are_immutable_dataclasses():
    record = ArticleGroupRecord(
        similar_group_id=10,
        cluster_id=7001,
        group_rank=1,
        representative_article_id=4001,
        algorithm_version='b4-v1',
        generated_at=GENERATED_AT,
    )
    member = ArticleGroupMemberRecord(
        similar_group_id=10,
        processed_article_id=4001,
        similarity_score=1.0,
        exact_duplicate_count=2,
        is_representative=True,
        article_rank=1,
    )
    duplicate = ExactDuplicateCountRecord(4001, 2)

    for value in (record, member, duplicate):
        assert is_dataclass(value)
        with pytest.raises(FrozenInstanceError):
            value.processed_article_id = 99  # type: ignore[misc]


@pytest.mark.parametrize(
    'processed_article_ids',
    [
        ['4001'],
        [4001.0],
        [True],
        [0],
        [-1],
        [4001, 4001],
    ],
)
def test_validate_ids_rejects_non_integer_non_positive_and_duplicate_ids(
    processed_article_ids,
):
    with pytest.raises(ValueError, match='processed article IDs'):
        ArticleGroupRepository._validate_ids(processed_article_ids)


@pytest.mark.anyio
async def test_exact_duplicate_counts_use_raw_mappings_only():
    session = RecordingAsyncSession(
        results=[
            DummyResult(
                [
                    {
                        'processed_article_id': 4001,
                        'exact_duplicate_count': 0,
                    },
                    {
                        'processed_article_id': 4002,
                        'exact_duplicate_count': 1,
                    },
                    {
                        'processed_article_id': 4003,
                        'exact_duplicate_count': 3,
                    },
                ]
            )
        ]
    )
    repository = ArticleGroupRepository(session)

    result = await repository.get_exact_duplicate_counts([4001, 4002, 4003])

    assert result == [
        ExactDuplicateCountRecord(4001, 0),
        ExactDuplicateCountRecord(4002, 1),
        ExactDuplicateCountRecord(4003, 3),
    ]
    sql = normalize_sql(session.statements[0]).lower()
    assert 'news_article_raw_processed_map' in sql
    assert 'count(distinct' in sql
    assert 'greatest(count(distinct' in sql
    assert 'news_article_processed' not in sql
    assert session.parameters[0]['processed_article_ids'] == [4001, 4002, 4003]


@pytest.mark.anyio
async def test_replace_cluster_groups_replaces_rows_and_updates_ready_status():
    session = RecordingAsyncSession(
        results=[
            DummyResult([{'id': 7001}]),
            DummyResult(
                [{'processed_article_id': 4001}, {'processed_article_id': 4002}]
            ),
            DummyResult([]),
            DummyResult([{'id': 71, 'group_rank': 1}]),
            DummyResult([{'id': 72, 'group_rank': 2}]),
        ]
    )
    repository = ArticleGroupRepository(session, now=lambda: GENERATED_AT)
    grouping = _result(_group(1, 4001, 4001), _group(2, 4002, 4002))

    await repository.replace_cluster_groups(
        7001,
        grouping,
        algorithm_version='b4-v1',
        generated_at=GENERATED_AT,
        exact_counts={4001: 0, 4002: 1},
    )

    assert session.commits == 0
    assert len(session.statements) == 7
    statements = [normalize_sql(statement).lower() for statement in session.statements]
    assert 'for update' in statements[0]
    assert 'select processed_article_id' in statements[1]
    assert 'for update' in statements[1]
    assert 'delete from stock.news_cluster_similar_group' in statements[2]
    assert 'insert into stock.news_cluster_similar_group' in statements[3]
    assert 'insert into stock.news_cluster_similar_group' in statements[4]
    assert 'insert into stock.news_cluster_similar_group_article' in statements[5]
    assert 'update stock.news_cluster' in statements[6]
    assert session.parameters[-1] == {
        'cluster_id': 7001,
        'generated_at': GENERATED_AT,
        'issue_code': None,
    }
    assert session.parameters[3]['algorithm_version'] == 'b4-v1'
    assert session.parameters[4]['algorithm_version'] == 'b4-v1'
    assert session.parameters[5][0]['similar_group_id'] == 71
    assert session.parameters[5][1]['similar_group_id'] == 72


@pytest.mark.anyio
async def test_replace_cluster_groups_rejects_membership_mismatch_and_rolls_back():
    session = RecordingAsyncSession(
        results=[
            DummyResult([{'id': 7001}]),
            DummyResult(
                [{'processed_article_id': 4001}, {'processed_article_id': 4002}]
            ),
        ]
    )
    repository = ArticleGroupRepository(session, now=lambda: GENERATED_AT)

    with pytest.raises(ValueError, match='exactly match'):
        await repository.replace_cluster_groups(
            7001,
            _result(_group(1, 4001, 4001)),
            algorithm_version='b4-v1',
            generated_at=GENERATED_AT,
            exact_counts={4001: 0, 4002: 0},
        )

    assert len(session.statements) == 2
    assert session.commits == 0
    assert session.rollbacks == 1


@pytest.mark.anyio
async def test_replace_cluster_groups_rolls_back_when_insert_fails():
    session = RecordingAsyncSession(
        results=[
            DummyResult([{'id': 7001}]),
            DummyResult([{'processed_article_id': 4001}]),
            DummyResult([]),
            DummyResult([]),
        ]
    )
    repository = ArticleGroupRepository(session, now=lambda: GENERATED_AT)

    with pytest.raises(RuntimeError, match='group id'):
        await repository.replace_cluster_groups(
            7001,
            _result(_group(1, 4001, 4001)),
            algorithm_version='b4-v1',
            generated_at=GENERATED_AT,
            exact_counts={4001: 0},
        )

    assert session.commits == 0
    assert session.rollbacks == 1


@pytest.mark.anyio
async def test_unavailable_singletons_preserve_exact_counts_and_fixed_status():
    session = RecordingAsyncSession(
        results=[
            DummyResult([{'id': 7001}]),
            DummyResult(
                [{'processed_article_id': 4002}, {'processed_article_id': 4001}]
            ),
            DummyResult([]),
            DummyResult([{'id': 71, 'group_rank': 1}]),
            DummyResult([{'id': 72, 'group_rank': 2}]),
        ]
    )
    repository = ArticleGroupRepository(session, now=lambda: GENERATED_AT)

    await repository.mark_grouping_unavailable_with_singletons(
        7001,
        [{'processed_article_id': 4001}, {'processed_article_id': 4002}],
        {4001: 0, 4002: 4},
        'b4-v1',
    )

    assert session.commits == 0
    update_params = session.parameters[-1]
    assert update_params == {
        'cluster_id': 7001,
        'generated_at': None,
        'issue_code': 'SIMILARITY_GROUPING_FAILED',
    }
    member_params = session.parameters[-2]
    assert [row['processed_article_id'] for row in member_params] == [4002, 4001]
    assert [row['exact_duplicate_count'] for row in member_params] == [4, 0]
    assert all(row['similarity_score'] == 1.0 for row in member_params)
    assert all(row['is_representative'] for row in member_params)


@pytest.mark.anyio
async def test_list_cluster_grouping_returns_ranked_stored_memberships():
    session = RecordingAsyncSession(
        results=[
            DummyResult(
                [
                    {
                        'status': 'READY',
                        'article_grouping_generated_at': GENERATED_AT,
                        'article_grouping_issue_code': None,
                    }
                ]
            ),
            DummyResult(
                [
                    {
                        'similar_group_id': 71,
                        'cluster_id': 7001,
                        'group_rank': 1,
                        'representative_article_id': 4001,
                        'algorithm_version': 'b4-v1',
                        'generated_at': GENERATED_AT,
                    }
                ]
            ),
            DummyResult(
                [
                    {
                        'similar_group_id': 71,
                        'processed_article_id': 4001,
                        'similarity_score': 1.0,
                        'exact_duplicate_count': 2,
                        'is_representative': True,
                        'article_rank': 1,
                    }
                ]
            ),
        ]
    )
    repository = ArticleGroupRepository(session)

    grouping = await repository.get_cluster_grouping(7001)

    assert grouping.status == 'READY'
    assert grouping.groups[0].group_rank == 1
    assert grouping.groups[0].members[0].processed_article_id == 4001
    assert grouping.groups[0].members[0].exact_duplicate_count == 2
    assert all(
        'embedding' not in normalize_sql(statement).lower()
        for statement in session.statements
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    'exact_counts',
    [
        {4001: 0},
        {4001: 0, 4002: 0, 4003: 0},
        [
            {'processed_article_id': 4001, 'exact_duplicate_count': 0},
            {'processed_article_id': 4001, 'exact_duplicate_count': 1},
        ],
        {4001: True, 4002: 0},
    ],
)
async def test_replace_cluster_groups_rejects_non_exact_counts(exact_counts):
    session = RecordingAsyncSession(
        results=[
            DummyResult([{'id': 7001}]),
            DummyResult(
                [{'processed_article_id': 4001}, {'processed_article_id': 4002}]
            ),
        ]
    )
    repository = ArticleGroupRepository(session, now=lambda: GENERATED_AT)

    with pytest.raises(ValueError, match='exact duplicate counts'):
        await repository.replace_cluster_groups(
            7001,
            _result(_group(1, 4001, 4001), _group(2, 4002, 4002)),
            algorithm_version='b4-v1',
            generated_at=GENERATED_AT,
            exact_counts=exact_counts,
        )

    assert session.statements[2:] == []
    assert session.rollbacks == 1


@pytest.mark.anyio
async def test_replace_cluster_groups_requires_nonblank_algorithm_version():
    session = RecordingAsyncSession(
        results=[
            DummyResult([{'id': 7001}]),
            DummyResult([{'processed_article_id': 4001}]),
        ]
    )
    repository = ArticleGroupRepository(session, now=lambda: GENERATED_AT)

    with pytest.raises(ValueError, match='algorithm_version'):
        await repository.replace_cluster_groups(
            7001,
            _result(_group(1, 4001, 4001)),
            algorithm_version='   ',
            generated_at=GENERATED_AT,
            exact_counts={4001: 0},
        )

    assert session.statements[2:] == []
    assert session.rollbacks == 1


@pytest.mark.anyio
@pytest.mark.parametrize('score', [nan, inf, -0.01, 1.01, True])
async def test_replace_cluster_groups_rejects_invalid_similarity_scores(score):
    session = RecordingAsyncSession(
        results=[
            DummyResult([{'id': 7001}]),
            DummyResult([{'processed_article_id': 4001}]),
        ]
    )
    repository = ArticleGroupRepository(session, now=lambda: GENERATED_AT)
    invalid_member = SimilarityGroupMember(
        processed_article_id=4001,
        similarity_score=score,
        is_representative=True,
        article_rank=1,
    )
    grouping = SimilarityGroupingResult(
        groups=(
            SimilarityGroup(
                group_rank=1,
                representative_article_id=4001,
                members=(invalid_member,),
            ),
        )
    )

    with pytest.raises(ValueError, match='similarity score'):
        await repository.replace_cluster_groups(
            7001,
            grouping,
            algorithm_version='b4-v1',
            generated_at=GENERATED_AT,
            exact_counts={4001: 0},
        )

    assert session.statements[2:] == []
    assert session.rollbacks == 1


@pytest.mark.anyio
async def test_cluster_article_query_correlates_group_members_to_cluster():
    from app.db.repositories.cluster_repo import ClusterRepository

    session = RecordingAsyncSession(results=[DummyResult([])])
    await ClusterRepository(session).get_cluster_articles(7001)

    sql = normalize_sql(session.statements[0]).lower()
    assert 'similar_group_id' in sql
    assert 'sg_scope.cluster_id = ca.cluster_id' in sql
