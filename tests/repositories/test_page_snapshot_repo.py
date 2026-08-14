from __future__ import annotations

from datetime import date

import pytest

pytest.importorskip('sqlalchemy')

from tests.support import (
    DummyResult,
    RecordingAsyncSession,
    jsonable,
    load_module,
    normalize_sql,
)

page_repo_module = load_module('app.db.repositories.page_snapshot_repo')
page_write_repo_module = load_module('app.db.repositories.page_snapshot_write_repo')

PageSnapshotRepository = page_repo_module.PageSnapshotRepository
PageSnapshotWriteRepository = page_write_repo_module.PageSnapshotWriteRepository


@pytest.mark.anyio
async def test_get_latest_page_header_orders_by_latest_business_date_then_version(
    sample_page_snapshot_row,
):
    session = RecordingAsyncSession(results=[DummyResult([sample_page_snapshot_row])])
    repo = PageSnapshotRepository(session)

    result = await repo.get_latest_page_header()

    assert jsonable(result)['id'] == sample_page_snapshot_row['id']
    assert len(session.statements) == 1
    sql = normalize_sql(session.statements[0])
    assert 'market_daily_page' in sql
    assert 'business_date desc' in sql
    assert 'version_no desc' in sql
    assert 'limit 1' in sql


@pytest.mark.anyio
async def test_get_latest_public_page_header_selects_ready_or_partial_before_ordering(
    sample_page_snapshot_row,
):
    session = RecordingAsyncSession(results=[DummyResult([sample_page_snapshot_row])])
    repo = PageSnapshotRepository(session)

    result = await repo.get_latest_public_page_header()

    assert jsonable(result)['id'] == sample_page_snapshot_row['id']
    sql = normalize_sql(session.statements[0]).lower()
    assert "where status in ('ready', 'partial')" in sql
    assert 'order by business_date desc, version_no desc, id desc' in sql
    assert sql.index("where status in ('ready', 'partial')") < sql.index(
        'order by business_date desc, version_no desc, id desc'
    )


@pytest.mark.anyio
async def test_get_page_header_by_business_date_without_version_selects_public_version(
    sample_page_snapshot_row,
):
    session = RecordingAsyncSession(results=[DummyResult([sample_page_snapshot_row])])
    repo = PageSnapshotRepository(session)

    result = await repo.get_page_header_by_business_date(
        sample_page_snapshot_row['business_date']
    )

    assert jsonable(result)['id'] == sample_page_snapshot_row['id']
    sql = normalize_sql(session.statements[0]).lower()
    assert "business_date = '2026-03-17'" in sql
    assert "status in ('ready', 'partial')" in sql
    assert 'order by business_date desc, version_no desc, id desc' in sql
    assert sql.index("status in ('ready', 'partial')") < sql.index(
        'order by business_date desc, version_no desc, id desc'
    )


@pytest.mark.anyio
async def test_get_page_header_by_business_date_with_version_keeps_failed_versions_available(
    sample_page_snapshot_row,
):
    failed_page = {**sample_page_snapshot_row, 'status': 'FAILED'}
    session = RecordingAsyncSession(results=[DummyResult([failed_page])])
    repo = PageSnapshotRepository(session)

    result = await repo.get_page_header_by_business_date(
        failed_page['business_date'], version_no=3
    )

    assert jsonable(result) == failed_page
    sql = normalize_sql(session.statements[0]).lower()
    statement = str(session.statements[0])
    assert ':business_date' in statement
    assert ':version_no' in statement
    assert "business_date = '2026-03-17'" in sql
    assert 'version_no = 3' in sql
    assert "status in ('ready', 'partial')" not in sql


@pytest.mark.anyio
async def test_get_page_header_by_id_keeps_failed_page_available(
    sample_page_snapshot_row,
):
    failed_page = {**sample_page_snapshot_row, 'status': 'FAILED'}
    session = RecordingAsyncSession(results=[DummyResult([failed_page])])
    repo = PageSnapshotRepository(session)

    result = await repo.get_page_header_by_id(failed_page['id'])

    assert jsonable(result) == failed_page
    sql = normalize_sql(session.statements[0]).lower()
    statement = str(session.statements[0])
    assert ':page_id' in statement
    assert f'id = {failed_page["id"]}' in sql
    assert "status in ('ready', 'partial')" not in sql


@pytest.mark.anyio
async def test_get_page_header_by_business_date_uses_explicit_version_when_provided(
    sample_page_snapshot_row,
):
    session = RecordingAsyncSession(results=[DummyResult([sample_page_snapshot_row])])
    repo = PageSnapshotRepository(session)

    result = await repo.get_page_header_by_business_date(
        sample_page_snapshot_row['business_date'], version_no=3
    )

    assert jsonable(result)['id'] == sample_page_snapshot_row['id']
    sql = normalize_sql(session.statements[0])
    assert 'business_date' in sql
    assert 'version_no' in sql
    assert 'order by' in sql
    assert 'is_latest' in sql


@pytest.mark.anyio
async def test_get_page_indices_includes_source_daily_index_id():
    session = RecordingAsyncSession(results=[DummyResult([])])
    repo = PageSnapshotRepository(session)

    result = await repo.get_page_indices([901])

    assert result == []
    sql = normalize_sql(session.statements[0])
    assert 'market_index_daily_id' in sql
    assert 'source_date' in sql
    assert 'expected_session_date' in sql
    assert 'session_close_at' in sql


@pytest.mark.anyio
async def test_get_page_article_links_excludes_legacy_null_public_identities():
    session = RecordingAsyncSession(results=[DummyResult([])])
    repo = PageSnapshotRepository(session)

    result = await repo.get_page_article_links([901])

    assert result == []
    sql = normalize_sql(session.statements[0]).lower()
    assert 'processed_article_id is not null' in sql
    assert 'cluster_uid is not null' in sql


@pytest.mark.anyio
@pytest.mark.parametrize(
    'field',
    ['processed_article_id', 'cluster_uid'],
    ids=['null-processed-article-id', 'null-cluster-uid'],
)
async def test_insert_page_article_link_rejects_null_public_identity(field):
    session = RecordingAsyncSession()
    repo = PageSnapshotWriteRepository(session)
    payload = {
        'page_market_id': 901,
        'display_order': 1,
        'processed_article_id': 4001,
        'cluster_id': 7001,
        'cluster_uid': 'cluster-uid',
        'cluster_title': '클러스터',
        'title': '기사',
        'publisher_name': '매체',
        'published_at': None,
        'origin_link': 'https://example.com/article',
        'naver_link': None,
    }
    payload[field] = None

    with pytest.raises(ValueError, match=field):
        await repo.insert_page_article_link(payload)

    assert session.statements == []


@pytest.mark.anyio
async def test_get_page_cluster_themes_reads_ranked_snapshot_rows_in_one_query():
    session = RecordingAsyncSession(
        results=[
            DummyResult(
                [
                    {
                        'page_market_cluster_id': 701,
                        'theme_code': 'THEME_A',
                        'rank': 1,
                    },
                    {
                        'page_market_cluster_id': 702,
                        'theme_code': 'THEME_B',
                        'rank': 2,
                    },
                ]
            )
        ]
    )
    repo = PageSnapshotRepository(session)

    result = await repo.get_page_cluster_themes([701, 702])

    assert result == [
        {
            'page_market_cluster_id': 701,
            'theme_code': 'THEME_A',
            'rank': 1,
        },
        {
            'page_market_cluster_id': 702,
            'theme_code': 'THEME_B',
            'rank': 2,
        },
    ]
    assert len(session.statements) == 1
    sql = normalize_sql(session.statements[0])
    assert 'market_daily_page_market_cluster_theme' in sql
    assert 'ORDER BY page_market_cluster_id, rank' in sql


@pytest.mark.anyio
async def test_exists_page_for_business_date_checks_date_boundary(sample_business_date):
    session = RecordingAsyncSession(results=[DummyResult([1])])
    repo = PageSnapshotRepository(session)

    result = await repo.exists_page_for_business_date(sample_business_date)

    assert result is True
    sql = normalize_sql(session.statements[0])
    assert 'select 1' in sql.lower()
    assert 'business_date' in sql


@pytest.mark.anyio
async def test_exists_public_page_for_business_date_ignores_failed_only_dates(
    sample_business_date,
):
    session = RecordingAsyncSession(results=[DummyResult([])])
    repo = PageSnapshotRepository(session)

    result = await repo.exists_public_page_for_business_date(sample_business_date)

    assert result is False
    sql = normalize_sql(session.statements[0]).lower()
    assert "business_date = '2026-03-17'" in sql
    assert "status in ('ready', 'partial')" in sql


@pytest.mark.anyio
async def test_get_latest_version_no_returns_latest_version(sample_business_date):
    session = RecordingAsyncSession(results=[DummyResult([3])])
    repo = PageSnapshotRepository(session)

    result = await repo.get_latest_version_no(sample_business_date)

    assert result == 3
    sql = normalize_sql(session.statements[0])
    assert 'max(version_no)' in sql.lower()


@pytest.mark.anyio
async def test_get_next_version_no_takes_advisory_lock_before_allocating_version(
    sample_business_date,
):
    session = RecordingAsyncSession(results=[DummyResult([None]), DummyResult([4])])
    repo = PageSnapshotWriteRepository(session)

    result = await repo.get_next_version_no(sample_business_date)

    assert result == 4
    assert len(session.statements) == 2
    lock_sql = normalize_sql(session.statements[0]).lower()
    allocation_sql = normalize_sql(session.statements[1]).lower()
    assert 'pg_advisory_xact_lock' in lock_sql
    assert 'max(version_no)' not in lock_sql
    assert 'max(version_no)' in allocation_sql
    assert session.parameters[0] == {
        'lock_key': page_write_repo_module._page_version_lock_key(sample_business_date)
    }
    assert session.parameters[1] == {'business_date': sample_business_date}


@pytest.mark.anyio
async def test_get_adjacent_business_dates_resolves_both_sides_in_one_query(
    sample_business_date,
):
    session = RecordingAsyncSession(
        results=[
            DummyResult(
                [
                    {
                        'previous_business_date': date(2026, 3, 13),
                        'next_business_date': date(2026, 3, 18),
                    }
                ]
            )
        ]
    )
    repo = PageSnapshotRepository(session)

    result = await repo.get_adjacent_business_dates(sample_business_date)

    assert result == {
        'previous_business_date': date(2026, 3, 13),
        'next_business_date': date(2026, 3, 18),
    }
    assert len(session.statements) == 1
    sql = normalize_sql(session.statements[0]).lower()
    assert 'max(business_date) filter' in sql
    assert 'min(business_date) filter' in sql
    assert "business_date < '2026-03-17'" in sql
    assert "business_date > '2026-03-17'" in sql
    # The date reaches SQL as a bound parameter, never as interpolated text.
    assert ':business_date' in str(session.statements[0])


@pytest.mark.anyio
async def test_get_adjacent_business_dates_returns_nulls_for_only_page(
    sample_business_date,
):
    session = RecordingAsyncSession(
        results=[
            DummyResult(
                [
                    {
                        'previous_business_date': None,
                        'next_business_date': None,
                    }
                ]
            )
        ]
    )
    repo = PageSnapshotRepository(session)

    result = await repo.get_adjacent_business_dates(sample_business_date)

    assert result == {
        'previous_business_date': None,
        'next_business_date': None,
    }


@pytest.mark.anyio
async def test_get_adjacent_public_business_dates_ignores_failed_only_dates(
    sample_business_date,
):
    session = RecordingAsyncSession(
        results=[
            DummyResult(
                [
                    {
                        'previous_business_date': date(2026, 3, 13),
                        'next_business_date': date(2026, 3, 18),
                    }
                ]
            )
        ]
    )
    repo = PageSnapshotRepository(session)

    result = await repo.get_adjacent_public_business_dates(sample_business_date)

    assert result == {
        'previous_business_date': date(2026, 3, 13),
        'next_business_date': date(2026, 3, 18),
    }
    sql = normalize_sql(session.statements[0]).lower()
    assert "where status in ('ready', 'partial')" in sql
    assert "business_date < '2026-03-17'" in sql
    assert "business_date > '2026-03-17'" in sql


@pytest.mark.anyio
async def test_list_page_versions_orders_newest_first_under_a_hard_limit(
    sample_business_date, sample_page_version_rows
):
    session = RecordingAsyncSession(results=[DummyResult(sample_page_version_rows)])
    repo = PageSnapshotRepository(session)

    result = await repo.list_page_versions(sample_business_date)

    assert [row['version_no'] for row in jsonable(result)] == [3, 2, 1]
    sql = normalize_sql(session.statements[0]).lower()
    assert "where business_date = '2026-03-17'" in sql
    assert 'order by version_no desc' in sql
    assert f'limit {page_repo_module.PAGE_VERSION_LIST_LIMIT}' in sql
    assert 'max(version_no) over (partition by business_date)' in sql
    assert ':business_date' in str(session.statements[0])
    assert ':limit' in str(session.statements[0])


@pytest.mark.anyio
async def test_list_page_versions_binds_a_caller_supplied_limit(sample_business_date):
    session = RecordingAsyncSession(results=[DummyResult([])])
    repo = PageSnapshotRepository(session)

    await repo.list_page_versions(sample_business_date, limit=5)

    assert 'limit 5' in normalize_sql(session.statements[0]).lower()


@pytest.mark.anyio
async def test_list_archive_page_headers_prefers_latest_version_per_day(
    sample_archive_list_payload,
):
    session = RecordingAsyncSession(
        results=[DummyResult(sample_archive_list_payload['items'])]
    )
    repo = PageSnapshotRepository(session)

    result = await repo.list_archive_page_headers(
        from_date=date(2026, 3, 16),
        to_date=date(2026, 3, 17),
        status='READY',
        page=1,
        size=30,
    )

    assert [item['pageId'] for item in jsonable(result)] == [501, 502]
    sql = normalize_sql(session.statements[0])
    assert 'business_date' in sql
    assert 'status = cast(upper(' in sql.lower()
    assert ('distinct on' in sql.lower()) or ('row_number()' in sql.lower())


@pytest.mark.anyio
async def test_archive_ready_filter_excludes_latest_partial_instead_of_old_ready():
    session = RecordingAsyncSession(results=[DummyResult([])])
    repo = PageSnapshotRepository(session)

    result = await repo.list_archive_page_headers(
        from_date=date(2026, 3, 16),
        to_date=date(2026, 3, 17),
        status='READY',
    )

    assert result == []
    sql = normalize_sql(session.statements[0]).lower()
    expected_cte = (
        'with latest_public as ( select distinct on (business_date) id, '
        'business_date, version_no, page_title, status, global_headline, '
        'search_document, '
        'generated_at, partial_message from stock.market_daily_page '
        "where status in ('ready', 'partial') order by business_date desc, "
        'version_no desc, id desc )'
    )
    assert expected_cte in sql
    cte_end = sql.index(') select id as "pageid"')
    assert "business_date >= '2026-03-16'" in sql[cte_end:]
    assert "business_date <= '2026-03-17'" in sql[cte_end:]
    assert 'status = cast(upper(' in sql[cte_end:]
    assert sql.endswith('order by business_date desc limit 30 offset 0')


@pytest.mark.anyio
async def test_archive_status_filter_is_bound_at_uppercase_boundary(
    sample_archive_list_payload,
):
    session = RecordingAsyncSession(
        results=[DummyResult(sample_archive_list_payload['items'])]
    )
    repo = PageSnapshotRepository(session)

    await repo.list_archive_page_headers(status='ready')

    sql = normalize_sql(session.statements[0])
    assert 'upper(' in sql.lower()


@pytest.mark.anyio
async def test_archive_query_scope_keeps_all_q_tokens_in_one_market_or_cluster_unit():
    session = RecordingAsyncSession(results=[DummyResult([])])
    repo = PageSnapshotRepository(session)

    await repo.list_archive_page_headers(query_tokens=['nvidia', 'earnings'])

    sql = normalize_sql(session.statements[0]).lower()
    assert "ilike '%nvidia%' escape '\\'" in sql
    assert "ilike '%earnings%' escape '\\'" in sql
    assert 'market_daily_page_market as market' in sql
    assert 'market_daily_page_market_cluster as cluster' in sql
    assert sql.index('with latest_public as') < sql.index('select id as "pageid"')
    assert ':q_token_0' in str(session.statements[0])
    assert ':q_token_1' in str(session.statements[0])


@pytest.mark.anyio
async def test_archive_q_only_scope_includes_one_combined_page_unit():
    session = RecordingAsyncSession(results=[DummyResult([])])
    repo = PageSnapshotRepository(session)

    await repo.list_archive_page_headers(query_tokens=['headline', 'korean'])

    sql = normalize_sql(session.statements[0]).lower()
    assert 'latest_public.search_document ilike' in sql
    assert 'lower(concat_ws' not in sql
    statement_sql = str(session.statements[0]).lower()
    page_scope = statement_sql[
        statement_sql.index('latest_public.search_document') : statement_sql.index(
            ' or exists'
        )
    ]
    assert page_scope.count('q_token_0') == 1
    assert page_scope.count('q_token_1') == 1


@pytest.mark.anyio
async def test_archive_market_and_theme_scopes_do_not_search_page_fields():
    market_session = RecordingAsyncSession(results=[DummyResult([])])
    theme_session = RecordingAsyncSession(results=[DummyResult([])])

    await PageSnapshotRepository(market_session).list_archive_page_headers(
        market_type='KR', query_tokens=['headline', 'korean']
    )
    await PageSnapshotRepository(theme_session).list_archive_page_headers(
        theme_codes=['ROOT'], query_tokens=['headline', 'korean']
    )

    assert 'latest_public.page_title' not in str(market_session.statements[0])
    assert 'latest_public.global_headline' not in str(market_session.statements[0])
    assert 'latest_public.page_title' not in str(theme_session.statements[0])
    assert 'latest_public.global_headline' not in str(theme_session.statements[0])


@pytest.mark.anyio
async def test_archive_theme_and_query_scope_correlates_theme_and_tokens_to_one_cluster():
    session = RecordingAsyncSession(results=[DummyResult([])])
    repo = PageSnapshotRepository(session)

    await repo.list_archive_page_headers(
        market_type='KR',
        theme_codes=['ROOT', 'ROOT_CHILD'],
        query_tokens=['nvidia', 'earnings'],
    )

    sql = normalize_sql(session.statements[0]).lower()
    assert 'market_daily_page_market_cluster_theme' in sql
    assert 'theme_code in' in sql
    assert 'market.market_type = cast' in sql
    assert 'cluster.search_document ilike' in sql
    assert sql.count('cluster.search_document ilike') == 2
    assert 'market.id = cluster.page_market_id' in sql
    assert 'market.page_id = latest_public.id' in sql


@pytest.mark.anyio
async def test_archive_count_and_list_share_filter_sql_and_bind_values():
    list_session = RecordingAsyncSession(results=[DummyResult([])])
    count_session = RecordingAsyncSession(results=[DummyResult([0])])
    list_repo = PageSnapshotRepository(list_session)
    count_repo = PageSnapshotRepository(count_session)
    kwargs = {
        'from_date': date(2026, 3, 16),
        'to_date': date(2026, 3, 17),
        'status': 'READY',
        'market_type': 'US',
        'theme_codes': ['A', 'B'],
        'query_tokens': ['100%_ready'],
    }

    await list_repo.list_archive_page_headers(**kwargs, page=2, size=10)
    await count_repo.count_archive_page_headers(**kwargs)

    list_sql = str(list_session.statements[0])
    count_sql = str(count_session.statements[0])
    list_where = list_sql[list_sql.index('FROM latest_public') :].split('ORDER BY', 1)[
        0
    ]
    count_where = count_sql[count_sql.index('FROM latest_public') :]
    assert list_where == count_where
    assert (
        list_session.statements[0]._bindparams.keys()
        >= count_session.statements[0]._bindparams.keys()
    )
    assert list_session.parameters[0] is None
    assert count_session.parameters[0] is None


@pytest.mark.anyio
async def test_archive_query_escapes_like_metacharacters_as_literal_substrings():
    session = RecordingAsyncSession(results=[DummyResult([])])
    repo = PageSnapshotRepository(session)

    await repo.list_archive_page_headers(query_tokens=['100%_ready\\now'])

    sql = normalize_sql(session.statements[0]).lower()
    assert "ilike '%100\\%\\_ready\\\\now%' escape '\\'" in sql
