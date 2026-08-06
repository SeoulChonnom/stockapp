from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import fields, is_dataclass
from typing import Any, TypeVar

from sqlalchemy.ext.asyncio import AsyncSession

T = TypeVar('T')


class PostgresRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    @staticmethod
    def _model_from_mapping(model_cls: type[T], mapping: Mapping[Any, Any]) -> T:
        if not is_dataclass(model_cls):
            return model_cls(**dict(mapping))  # type: ignore[misc]

        allowed_fields = {field.name for field in fields(model_cls)}
        payload = {
            str(key): value for key, value in mapping.items() if key in allowed_fields
        }
        return model_cls(**payload)

    @classmethod
    def _models_from_mappings(
        cls, model_cls: type[T], mappings: Sequence[Mapping[Any, Any]]
    ) -> list[T]:
        return [cls._model_from_mapping(model_cls, mapping) for mapping in mappings]

    @staticmethod
    def _normalize_pagination(
        page: int,
        size: int,
        *,
        max_size: int | None = None,
    ) -> tuple[int, int, int]:
        page = max(page, 1)
        size = max(size, 1)
        if max_size is not None:
            size = min(size, max_size)
        offset = (page - 1) * size
        return page, size, offset

    @staticmethod
    def _row_to_dict(row: object) -> dict:
        mapping = getattr(row, '_mapping', None)
        if mapping is not None:
            return dict(mapping)
        return dict(row)  # type: ignore[arg-type]

    @staticmethod
    def _first_row(result: object) -> object | None:
        if hasattr(result, 'one_or_none'):
            return result.one_or_none()  # type: ignore[no-any-return]
        rows = result.all()  # type: ignore[no-any-return]
        return rows[0] if rows else None
