from __future__ import annotations

from datetime import date

import pytest

from tests.support import load_module

archive_service_module = load_module('app.domains.archive.service')
archive_assembler_module = load_module('app.domains.archive.assembler')
projections_module = load_module('app.db.repositories.projections')

ArchiveService = archive_service_module.ArchiveService
ValidationError = archive_service_module.ValidationError
ThemeCatalogError = archive_assembler_module.ThemeCatalogError
assemble_theme_catalog_response = (
    archive_assembler_module.assemble_theme_catalog_response
)
ThemeCatalogRecord = projections_module.ThemeCatalogRecord


class RecordingArchiveRepository:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def list_archive_page_headers(self, **kwargs: object) -> list[dict]:
        self.calls.append(('list_archive_page_headers', kwargs))
        return []

    async def count_archive_page_headers(self, **kwargs: object) -> int:
        self.calls.append(('count_archive_page_headers', kwargs))
        return 0


class RecordingThemeRepository:
    def __init__(self, rows: list[ThemeCatalogRecord] | None = None) -> None:
        self.rows = rows or []
        self.calls = 0

    async def list_active_tree_rows(self) -> list[ThemeCatalogRecord]:
        self.calls += 1
        return self.rows


@pytest.mark.anyio
async def test_archive_service_rejects_failed_status_before_querying_repository():
    repository = RecordingArchiveRepository()
    theme_repository = RecordingThemeRepository()
    service = ArchiveService(repository, theme_repository)

    with pytest.raises(ValidationError) as exc_info:
        await service.list_archive(
            from_date=date(2026, 3, 16),
            to_date=date(2026, 3, 17),
            status='FAILED',
            page=1,
            size=30,
        )

    assert exc_info.value.code == 'UNSUPPORTED_ARCHIVE_STATUS'
    assert repository.calls == []


def _theme_record(
    code: str,
    *,
    parent_code: str | None = None,
    sort_order: int = 1,
) -> ThemeCatalogRecord:
    return ThemeCatalogRecord(
        code=code,
        parent_code=parent_code,
        label=code,
        description=f'{code} description',
        sort_order=sort_order,
        is_active=True,
    )


@pytest.mark.anyio
async def test_archive_service_builds_theme_catalog_from_theme_repository():
    theme_repository = RecordingThemeRepository(
        [
            _theme_record('ROOT_B', sort_order=2),
            _theme_record('ROOT_A_CHILD_B', parent_code='ROOT_A', sort_order=2),
            _theme_record('ROOT_A', sort_order=1),
            _theme_record('ROOT_A_CHILD_A', parent_code='ROOT_A', sort_order=1),
        ]
    )
    service = ArchiveService(RecordingArchiveRepository(), theme_repository)

    result = await service.list_theme_catalog()

    assert [node.code for node in result] == ['ROOT_A', 'ROOT_B']
    assert [node.code for node in result[0].children] == [
        'ROOT_A_CHILD_A',
        'ROOT_A_CHILD_B',
    ]
    assert result[0].children[0].children == []
    assert theme_repository.calls == 1


def test_theme_catalog_assembler_supports_arbitrary_depth_without_recursion():
    depth = 1_200
    rows = [
        _theme_record(
            f'THEME_{index}',
            parent_code=f'THEME_{index - 1}' if index else None,
            sort_order=index,
        )
        for index in reversed(range(depth))
    ]

    roots = assemble_theme_catalog_response(rows)

    assert [node.code for node in roots] == ['THEME_0']
    node = roots[0]
    for index in range(1, depth):
        assert len(node.children) == 1
        node = node.children[0]
        assert node.code == f'THEME_{index}'
    assert node.children == []


@pytest.mark.parametrize(
    ('rows', 'expected_message'),
    [
        (
            [_theme_record('DUPLICATE'), _theme_record('DUPLICATE')],
            'duplicate',
        ),
        (
            [_theme_record('ORPHAN', parent_code='MISSING')],
            'orphan',
        ),
        (
            [_theme_record('SELF', parent_code='SELF')],
            'cycle',
        ),
        (
            [
                _theme_record('A', parent_code='B'),
                _theme_record('B', parent_code='A'),
            ],
            'cycle',
        ),
    ],
)
def test_theme_catalog_assembler_rejects_corrupt_catalog(rows, expected_message):
    with pytest.raises(ThemeCatalogError, match=expected_message):
        assemble_theme_catalog_response(rows)


def test_theme_catalog_assembler_returns_empty_tree_for_empty_catalog():
    assert assemble_theme_catalog_response([]) == []
