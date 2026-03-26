from __future__ import annotations

from dataclasses import fields, is_dataclass
from typing import Any, Mapping, Sequence, TypeVar

from sqlalchemy.ext.asyncio import AsyncSession

T = TypeVar("T")


class PostgresRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    @staticmethod
    def _model_from_mapping(model_cls: type[T], mapping: Mapping[str, Any]) -> T:
        if not is_dataclass(model_cls):
            return model_cls(**dict(mapping))  # type: ignore[misc]

        allowed_fields = {field.name for field in fields(model_cls)}
        payload = {key: value for key, value in mapping.items() if key in allowed_fields}
        return model_cls(**payload)

    @classmethod
    def _models_from_mappings(
        cls, model_cls: type[T], mappings: Sequence[Mapping[str, Any]]
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
