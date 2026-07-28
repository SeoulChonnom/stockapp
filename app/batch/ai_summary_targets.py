from __future__ import annotations

from dataclasses import dataclass

from app.db.enums import AiSummaryType


@dataclass(frozen=True, slots=True)
class AiSummaryTarget:
    """Stable identity for one generated AI summary."""

    target_key: str
    summary_type: str
    market_type: str | None
    cluster_id: int | None


def build_ai_summary_target_key(
    summary_type: str,
    *,
    market_type: str | None,
    cluster_id: int | None,
) -> str:
    """Build the persistent identity shared by source and retry summaries."""
    if summary_type == AiSummaryType.GLOBAL_HEADLINE.value:
        return AiSummaryType.GLOBAL_HEADLINE.value
    if summary_type == AiSummaryType.MARKET_SUMMARY.value:
        if not market_type:
            raise ValueError('MARKET_SUMMARY requires market_type.')
        return f'{AiSummaryType.MARKET_SUMMARY.value}:{market_type}'
    if summary_type in {
        AiSummaryType.CLUSTER_CARD_SUMMARY.value,
        AiSummaryType.CLUSTER_DETAIL_ANALYSIS.value,
    }:
        if cluster_id is None:
            raise ValueError(f'{summary_type} requires cluster_id.')
        return f'{summary_type}:{cluster_id}'
    raise ValueError(f'Unsupported AI summary type: {summary_type}')


def target_from_summary(summary: object) -> AiSummaryTarget:
    """Return a typed target from an AI summary projection."""
    summary_type = str(summary.summary_type)  # type: ignore[attr-defined]
    market_type = getattr(summary, 'market_type', None)
    cluster_id = getattr(summary, 'cluster_id', None)
    target_key = getattr(summary, 'target_key', None) or build_ai_summary_target_key(
        summary_type,
        market_type=market_type,
        cluster_id=cluster_id,
    )
    return AiSummaryTarget(
        target_key=target_key,
        summary_type=summary_type,
        market_type=market_type,
        cluster_id=cluster_id,
    )


__all__ = [
    'AiSummaryTarget',
    'build_ai_summary_target_key',
    'target_from_summary',
]
