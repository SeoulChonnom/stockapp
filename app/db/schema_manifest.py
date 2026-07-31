from __future__ import annotations

import json
import re
from copy import deepcopy
from typing import Any

from sqlalchemy import Connection, create_engine, text

from app.core.settings import get_settings

SCHEMA_MANIFEST_FORMAT = 1

_LEGACY_NULLABILITY_GUARDS = {
    'chk_market_index_daily_source_date_present': ('source_date', 'date'),
    'chk_market_index_daily_expected_session_date_present': (
        'expected_session_date',
        'date',
    ),
    'chk_market_index_daily_session_close_at_present': (
        'session_close_at',
        'timestamp with time zone',
    ),
}
_LEGACY_SOURCE_DATE_CONSTRAINT = 'chk_market_index_daily_source_not_future'


def _normalize_sql(value: object, *, schema: str) -> str:
    normalized = str(value or '')
    normalized = normalized.replace(f'"{schema}".', '').replace(f'{schema}.', '')
    normalized = normalized.replace('"', '')
    return re.sub(r'\s+', ' ', normalized).strip()


def _rows_as_dicts(
    connection: Connection,
    sql: str,
    *,
    schema: str,
) -> list[dict[str, Any]]:
    rows = connection.execute(text(sql), {'schema': schema}).mappings().all()
    return [
        {
            str(key): (
                _normalize_sql(value, schema=schema)
                if key
                in {
                    'data_type',
                    'default_expression',
                    'definition',
                    'result_type',
                }
                else value
            )
            for key, value in row.items()
        }
        for row in rows
    ]


def collect_schema_manifest(
    connection: Connection,
    *,
    schema: str,
) -> dict[str, Any]:
    """Collect deterministic PostgreSQL catalog metadata for schema adoption."""
    tables = _rows_as_dicts(
        connection,
        """
        SELECT class_.relname AS table_name,
               class_.relkind AS relation_kind
        FROM pg_class AS class_
        JOIN pg_namespace AS namespace_
          ON namespace_.oid = class_.relnamespace
        WHERE namespace_.nspname = :schema
          AND class_.relkind IN ('r', 'p')
          AND class_.relname <> 'alembic_version'
          AND NOT EXISTS (
              SELECT 1
              FROM pg_depend AS dependency_
              WHERE dependency_.classid = 'pg_class'::regclass
                AND dependency_.objid = class_.oid
                AND dependency_.deptype = 'e'
          )
        ORDER BY class_.relname
        """,
        schema=schema,
    )
    columns = _rows_as_dicts(
        connection,
        """
        SELECT class_.relname AS table_name,
               attribute_.attname AS column_name,
               pg_catalog.format_type(
                   attribute_.atttypid,
                   attribute_.atttypmod
               ) AS data_type,
               attribute_.attnotnull AS not_null,
               attribute_.attidentity AS identity_kind,
               COALESCE(
                   pg_get_expr(default_.adbin, default_.adrelid, TRUE),
                   ''
               ) AS default_expression
        FROM pg_attribute AS attribute_
        JOIN pg_class AS class_ ON class_.oid = attribute_.attrelid
        JOIN pg_namespace AS namespace_
          ON namespace_.oid = class_.relnamespace
        LEFT JOIN pg_attrdef AS default_
          ON default_.adrelid = attribute_.attrelid
         AND default_.adnum = attribute_.attnum
        WHERE namespace_.nspname = :schema
          AND class_.relkind IN ('r', 'p')
          AND class_.relname <> 'alembic_version'
          AND attribute_.attnum > 0
          AND NOT attribute_.attisdropped
          AND NOT EXISTS (
              SELECT 1
              FROM pg_depend AS dependency_
              WHERE dependency_.classid = 'pg_class'::regclass
                AND dependency_.objid = class_.oid
                AND dependency_.deptype = 'e'
          )
        ORDER BY class_.relname, attribute_.attname
        """,
        schema=schema,
    )
    constraints = _rows_as_dicts(
        connection,
        """
        SELECT class_.relname AS table_name,
               constraint_.conname AS constraint_name,
               constraint_.contype AS constraint_type,
               pg_get_constraintdef(constraint_.oid, TRUE) AS definition,
               constraint_.convalidated AS validated,
               constraint_.condeferrable AS deferrable,
               constraint_.condeferred AS initially_deferred
        FROM pg_constraint AS constraint_
        JOIN pg_class AS class_ ON class_.oid = constraint_.conrelid
        JOIN pg_namespace AS namespace_
          ON namespace_.oid = constraint_.connamespace
        WHERE namespace_.nspname = :schema
          AND class_.relname <> 'alembic_version'
          AND NOT EXISTS (
              SELECT 1
              FROM pg_depend AS dependency_
              WHERE dependency_.classid = 'pg_constraint'::regclass
                AND dependency_.objid = constraint_.oid
                AND dependency_.deptype = 'e'
          )
        ORDER BY class_.relname, constraint_.conname
        """,
        schema=schema,
    )
    indexes = _rows_as_dicts(
        connection,
        """
        SELECT table_.relname AS table_name,
               index_.relname AS index_name,
               index_metadata_.indisunique AS unique_index,
               index_metadata_.indisprimary AS primary_index,
               pg_get_indexdef(index_.oid, 0, TRUE) AS definition
        FROM pg_index AS index_metadata_
        JOIN pg_class AS index_
          ON index_.oid = index_metadata_.indexrelid
        JOIN pg_class AS table_
          ON table_.oid = index_metadata_.indrelid
        JOIN pg_namespace AS namespace_
          ON namespace_.oid = table_.relnamespace
        WHERE namespace_.nspname = :schema
          AND table_.relname <> 'alembic_version'
          AND NOT EXISTS (
              SELECT 1
              FROM pg_depend AS dependency_
              WHERE dependency_.classid = 'pg_class'::regclass
                AND dependency_.objid = index_.oid
                AND dependency_.deptype = 'e'
          )
        ORDER BY table_.relname, index_.relname
        """,
        schema=schema,
    )
    enums = _rows_as_dicts(
        connection,
        """
        SELECT type_.typname AS enum_name,
               enum_.enumsortorder::float AS sort_order,
               enum_.enumlabel AS label
        FROM pg_enum AS enum_
        JOIN pg_type AS type_ ON type_.oid = enum_.enumtypid
        JOIN pg_namespace AS namespace_
          ON namespace_.oid = type_.typnamespace
        WHERE namespace_.nspname = :schema
          AND NOT EXISTS (
              SELECT 1
              FROM pg_depend AS dependency_
              WHERE dependency_.classid = 'pg_type'::regclass
                AND dependency_.objid = type_.oid
                AND dependency_.deptype = 'e'
          )
        ORDER BY type_.typname, enum_.enumsortorder
        """,
        schema=schema,
    )
    functions = _rows_as_dicts(
        connection,
        """
        SELECT procedure_.proname AS function_name,
               pg_get_function_identity_arguments(procedure_.oid) AS arguments,
               pg_get_function_result(procedure_.oid) AS result_type,
               procedure_.prokind AS function_kind,
               pg_get_functiondef(procedure_.oid) AS definition
        FROM pg_proc AS procedure_
        JOIN pg_namespace AS namespace_
          ON namespace_.oid = procedure_.pronamespace
        WHERE namespace_.nspname = :schema
          AND NOT EXISTS (
              SELECT 1
              FROM pg_depend AS dependency_
              WHERE dependency_.classid = 'pg_proc'::regclass
                AND dependency_.objid = procedure_.oid
                AND dependency_.deptype = 'e'
          )
        ORDER BY procedure_.proname,
                 pg_get_function_identity_arguments(procedure_.oid)
        """,
        schema=schema,
    )
    triggers = _rows_as_dicts(
        connection,
        """
        SELECT class_.relname AS table_name,
               trigger_.tgname AS trigger_name,
               pg_get_triggerdef(trigger_.oid, TRUE) AS definition
        FROM pg_trigger AS trigger_
        JOIN pg_class AS class_ ON class_.oid = trigger_.tgrelid
        JOIN pg_namespace AS namespace_
          ON namespace_.oid = class_.relnamespace
        WHERE namespace_.nspname = :schema
          AND NOT trigger_.tgisinternal
          AND class_.relname <> 'alembic_version'
          AND NOT EXISTS (
              SELECT 1
              FROM pg_depend AS dependency_
              WHERE dependency_.classid = 'pg_trigger'::regclass
                AND dependency_.objid = trigger_.oid
                AND dependency_.deptype = 'e'
          )
        ORDER BY class_.relname, trigger_.tgname
        """,
        schema=schema,
    )
    seed_rows = _rows_as_dicts(
        connection,
        """
        SELECT provider_name,
               market_type::text AS market_type,
               keyword,
               is_active,
               priority
        FROM stock.news_search_keyword
        WHERE provider_name = 'NAVER_NEWS'
          AND keyword IN ('미국 증시', '코스피')
        ORDER BY market_type, keyword
        """,
        schema=schema,
    )
    return {
        'format': SCHEMA_MANIFEST_FORMAT,
        'schema': schema,
        'tables': tables,
        'columns': columns,
        'constraints': constraints,
        'indexes': indexes,
        'enums': enums,
        'functions': functions,
        'triggers': triggers,
        'seed_rows': seed_rows,
    }


def _compact_constraint_definition(value: object) -> str:
    return re.sub(r'[\s()]', '', str(value)).upper()


def canonicalize_migration_05_legacy_compatibility(
    manifest: dict[str, Any],
    *,
    canonical_manifest: dict[str, Any],
) -> dict[str, Any]:
    """Normalize only the nullable-column compatibility used by migration 05."""
    normalized = deepcopy(manifest)
    constraints = normalized['constraints']
    guard_names: set[str] = set()

    for guard_name, (
        column_name,
        expected_data_type,
    ) in _LEGACY_NULLABILITY_GUARDS.items():
        guard = next(
            (
                item
                for item in constraints
                if item['table_name'] == 'market_index_daily'
                and item['constraint_name'] == guard_name
            ),
            None,
        )
        if guard is None:
            continue
        expected_definition = f'CHECK{column_name.upper()}ISNOTNULLNOTVALID'
        if (
            guard['constraint_type'] != 'c'
            or guard['validated'] is not False
            or guard['deferrable'] is not False
            or guard['initially_deferred'] is not False
            or _compact_constraint_definition(guard['definition'])
            != expected_definition
        ):
            continue
        column = next(
            (
                item
                for item in normalized['columns']
                if item['table_name'] == 'market_index_daily'
                and item['column_name'] == column_name
            ),
            None,
        )
        if column is None or column['data_type'] != expected_data_type:
            continue
        column['not_null'] = True
        guard_names.add(guard_name)

    if guard_names != set(_LEGACY_NULLABILITY_GUARDS):
        return normalized

    normalized['constraints'] = [
        item for item in constraints if item['constraint_name'] not in guard_names
    ]
    legacy_source_constraint = next(
        (
            item
            for item in normalized['constraints']
            if item['table_name'] == 'market_index_daily'
            and item['constraint_name'] == _LEGACY_SOURCE_DATE_CONSTRAINT
        ),
        None,
    )
    canonical_source_constraint = next(
        (
            item
            for item in canonical_manifest['constraints']
            if item['table_name'] == 'market_index_daily'
            and item['constraint_name'] == _LEGACY_SOURCE_DATE_CONSTRAINT
        ),
        None,
    )
    expected_legacy_definition = (
        'CHECKsource_dateISNULLORexpected_session_dateISNULLOR'
        'source_date<=expected_session_date'
    ).upper()
    legacy_metadata = {
        key: value
        for key, value in (legacy_source_constraint or {}).items()
        if key != 'definition'
    }
    canonical_metadata = {
        key: value
        for key, value in (canonical_source_constraint or {}).items()
        if key != 'definition'
    }
    if (
        legacy_source_constraint is not None
        and canonical_source_constraint is not None
        and legacy_metadata == canonical_metadata
        and _compact_constraint_definition(legacy_source_constraint['definition'])
        == expected_legacy_definition
    ):
        legacy_source_constraint['definition'] = canonical_source_constraint[
            'definition'
        ]

    return normalized


def manifest_mismatch_sections(
    actual: dict[str, Any],
    expected: dict[str, Any],
) -> tuple[str, ...]:
    """Return manifest sections that differ without exposing schema data values."""
    return tuple(
        section
        for section in (
            'format',
            'schema',
            'tables',
            'columns',
            'constraints',
            'indexes',
            'enums',
            'functions',
            'triggers',
            'seed_rows',
        )
        if actual.get(section) != expected.get(section)
    )


def main() -> None:
    """Print a sanitized deterministic manifest using environment configuration."""
    settings = get_settings()
    engine = create_engine(settings.database_url, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            manifest = collect_schema_manifest(
                connection,
                schema=settings.database_schema,
            )
    finally:
        engine.dispose()
    print(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == '__main__':
    main()


__all__ = [
    'canonicalize_migration_05_legacy_compatibility',
    'collect_schema_manifest',
    'manifest_mismatch_sections',
]
