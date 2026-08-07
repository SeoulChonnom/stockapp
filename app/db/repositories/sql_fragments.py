"""Shared SQL text fragments reused across batch job repositories."""

from __future__ import annotations

DURATION_SECONDS_EXPR = 'GREATEST(EXTRACT(EPOCH FROM (now() - started_at))::int, 0)'

STEP_DURATION_MS_EXPR = (
    'GREATEST((EXTRACT(EPOCH FROM (now() - started_at)) * 1000)::int, 0)'
)

_LEASE_NULL_COLUMNS: tuple[str, ...] = (
    'lease_owner = NULL',
    'lease_token = NULL',
    'lease_expires_at = NULL',
    'heartbeat_at = NULL',
)


def lease_null_assignments_sql(indent: str) -> str:
    """Join the lease-reset SET assignments using the given continuation indent."""
    return f',\n{indent}'.join(_LEASE_NULL_COLUMNS) + ','


__all__ = [
    'DURATION_SECONDS_EXPR',
    'STEP_DURATION_MS_EXPR',
    'lease_null_assignments_sql',
]
