from __future__ import annotations

from datetime import date

from sqlalchemy import text

from app.core.settings import get_settings

from .base import PostgresRepository
from .projections import MarketIndexDailyCreateParams, MarketIndexDailyRecord


def _qualified_table(table_name: str) -> str:
    return f"{get_settings().database_schema}.{table_name}"


class MarketIndexRepository(PostgresRepository):
    async def upsert_index(self, params: MarketIndexDailyCreateParams) -> MarketIndexDailyRecord:
        statement = text(
            """
            INSERT INTO {index_table} (
                business_date,
                market_type,
                index_code,
                index_name,
                close_price,
                change_value,
                change_percent,
                high_price,
                low_price,
                currency_code,
                provider_name
            )
            VALUES (
                :business_date,
                CAST(:market_type AS {market_type_enum}),
                :index_code,
                :index_name,
                :close_price,
                :change_value,
                :change_percent,
                :high_price,
                :low_price,
                :currency_code,
                :provider_name
            )
            ON CONFLICT (business_date, market_type, index_code) DO UPDATE
            SET
                index_name = EXCLUDED.index_name,
                close_price = EXCLUDED.close_price,
                change_value = EXCLUDED.change_value,
                change_percent = EXCLUDED.change_percent,
                high_price = EXCLUDED.high_price,
                low_price = EXCLUDED.low_price,
                currency_code = EXCLUDED.currency_code,
                provider_name = EXCLUDED.provider_name,
                collected_at = now()
            RETURNING
                id AS market_index_daily_id,
                business_date,
                market_type,
                index_code,
                index_name,
                close_price,
                change_value,
                change_percent,
                high_price,
                low_price,
                currency_code,
                provider_name,
                collected_at,
                created_at
            """
            .format(
                index_table=_qualified_table("market_index_daily"),
                market_type_enum=_qualified_table("market_type_enum"),
            )
        )
        result = await self.session.execute(
            statement,
            {
                "business_date": params.business_date,
                "market_type": params.market_type,
                "index_code": params.index_code,
                "index_name": params.index_name,
                "close_price": params.close_price,
                "change_value": params.change_value,
                "change_percent": params.change_percent,
                "high_price": params.high_price,
                "low_price": params.low_price,
                "currency_code": params.currency_code,
                "provider_name": params.provider_name,
            },
        )
        row = result.mappings().one()
        return self._model_from_mapping(MarketIndexDailyRecord, row)

    async def list_indices_by_business_date(
        self,
        business_date: date,
    ) -> list[MarketIndexDailyRecord]:
        statement = text(
            """
            SELECT
                id AS market_index_daily_id,
                business_date,
                market_type,
                index_code,
                index_name,
                close_price,
                change_value,
                change_percent,
                high_price,
                low_price,
                currency_code,
                provider_name,
                collected_at,
                created_at
            FROM {index_table}
            WHERE business_date = :business_date
            ORDER BY market_type ASC, index_code ASC
            """
            .format(index_table=_qualified_table("market_index_daily"))
        )
        result = await self.session.execute(statement, {"business_date": business_date})
        return self._models_from_mappings(MarketIndexDailyRecord, result.mappings().all())


__all__ = ["MarketIndexRepository"]
