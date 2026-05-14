from __future__ import annotations

import re

_POSTGRES_IDENTIFIER_PATTERN = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')


def validate_postgres_identifier(identifier: str, *, kind: str = 'identifier') -> str:
    if not isinstance(identifier, str):
        raise TypeError(f'PostgreSQL {kind} must be a string.')

    normalized = identifier.strip()
    if not normalized or not _POSTGRES_IDENTIFIER_PATTERN.fullmatch(normalized):
        raise ValueError(f'Invalid PostgreSQL {kind}: {identifier!r}')
    return normalized


def quote_postgres_identifier(identifier: str, *, kind: str = 'identifier') -> str:
    validated_identifier = validate_postgres_identifier(identifier, kind=kind)
    return f'"{validated_identifier}"'


def qualify_db_identifier(identifier: str, *, schema: str | None = None) -> str:
    validated_schema = validate_postgres_identifier(
        _resolve_schema(schema),
        kind='schema',
    )
    validated_identifier = validate_postgres_identifier(identifier)
    return f'{validated_schema}.{validated_identifier}'


def build_search_path_sql(schema: str | None = None) -> str:
    validated_schema = quote_postgres_identifier(_resolve_schema(schema), kind='schema')
    return f'SET search_path TO {validated_schema}, public'


def _resolve_schema(schema: str | None) -> str:
    if schema is not None:
        return schema

    from app.core.settings import get_settings

    return get_settings().database_schema


__all__ = [
    'build_search_path_sql',
    'qualify_db_identifier',
    'quote_postgres_identifier',
    'validate_postgres_identifier',
]
