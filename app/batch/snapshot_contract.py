"""Runtime contracts shared by page snapshot writers."""

from __future__ import annotations


def require_snapshot_cluster_id(value: object) -> int:
    """Require a positive integer returned by a snapshot cluster insert."""
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise RuntimeError(
            'insert_page_market_cluster must return a positive integer id'
        )
    return value


__all__ = ['require_snapshot_cluster_id']
