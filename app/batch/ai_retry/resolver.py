from __future__ import annotations

from collections.abc import Iterable, Mapping

from app.batch.ai_output_contracts import KEY_POINT_FAILURE
from app.batch.ai_retry.models import AiRetryCounts, AiRetrySelection
from app.batch.ai_summary_targets import target_from_summary
from app.db.enums import AiSummaryStatus
from app.db.repositories.projections import AiSummaryRecord


def is_successful_summary(summary: AiSummaryRecord) -> bool:
    """Return whether a summary is a usable provider success."""
    return summary.status == AiSummaryStatus.SUCCESS.value and not summary.fallback_used


def has_unresolved_key_points(summary: AiSummaryRecord) -> bool:
    """Return whether a successful global headline still needs key points."""
    if summary.summary_type != 'GLOBAL_HEADLINE':
        return False
    metadata = summary.metadata_json
    if not isinstance(metadata, Mapping):
        return False
    issue = metadata.get('keyPointIssue')
    return isinstance(issue, Mapping) and issue.get('code') == KEY_POINT_FAILURE['code']


def is_retry_resolved(summary: AiSummaryRecord) -> bool:
    """Return whether all public outputs for a summary target are usable."""
    return is_successful_summary(summary) and not has_unresolved_key_points(summary)


def resolve_effective_summaries(
    summaries: Iterable[AiSummaryRecord],
) -> dict[str, AiSummaryRecord]:
    """Resolve SUCCESS first and otherwise the newest retry for each target."""
    grouped: dict[str, list[AiSummaryRecord]] = {}
    for summary in summaries:
        target_key = target_from_summary(summary).target_key
        grouped.setdefault(target_key, []).append(summary)

    effective: dict[str, AiSummaryRecord] = {}
    for target_key, candidates in grouped.items():
        successful = [row for row in candidates if is_successful_summary(row)]
        pool = successful or candidates
        effective[target_key] = max(pool, key=_summary_order)
    return effective


def select_retry_targets(
    *,
    source_job_id: int,
    retry_job_id: int,
    lineage: list[AiSummaryRecord],
) -> list[AiRetrySelection]:
    """Select only unresolved source targets, preserving crash-resume state."""
    source_rows = [row for row in lineage if row.batch_job_id == source_job_id]
    current_rows = {
        target_from_summary(row).target_key: row
        for row in lineage
        if row.batch_job_id == retry_job_id
    }
    effective = resolve_effective_summaries(lineage)
    selected: list[AiRetrySelection] = []
    for source_row in source_rows:
        target = target_from_summary(source_row)
        current = current_rows.get(target.target_key)
        resolved = effective.get(target.target_key, source_row)
        if is_retry_resolved(resolved):
            continue
        if current is not None and is_retry_resolved(current):
            continue

        if current is None:
            retry_source = resolved
        else:
            retry_source = _find_retry_source(
                current=current,
                lineage=lineage,
                fallback=source_row,
            )
        selected.append(
            AiRetrySelection(
                target=target,
                source_summary=retry_source,
                existing_retry=current,
            )
        )
    return selected


def calculate_retry_counts(
    *,
    source_job_id: int,
    retry_job_id: int,
    lineage: list[AiSummaryRecord],
) -> AiRetryCounts:
    """Calculate stable target-level counts after an attempt or resume."""
    source_rows = [row for row in lineage if row.batch_job_id == source_job_id]
    source_targets = {target_from_summary(row).target_key for row in source_rows}
    effective = resolve_effective_summaries(lineage)
    retry_rows = [
        row
        for row in lineage
        if row.batch_job_id == retry_job_id
        and target_from_summary(row).target_key in source_targets
    ]
    counts = AiRetryCounts(
        target_count=len(source_targets),
        attempted_count=len(
            {target_from_summary(row).target_key for row in retry_rows}
        ),
    )
    for target_key in source_targets:
        row = effective[target_key]
        if is_retry_resolved(row):
            counts.success_count += 1
        elif row.status == AiSummaryStatus.FAILED.value:
            counts.failed_count += 1
        else:
            counts.fallback_count += 1

    for retry_row in retry_rows:
        if not is_successful_summary(retry_row):
            continue
        source = _find_retry_source(
            current=retry_row,
            lineage=lineage,
            fallback=retry_row,
        )
        if not is_retry_resolved(source):
            counts.recovered_count += 1
    return counts


def _find_retry_source(
    *,
    current: AiSummaryRecord,
    lineage: list[AiSummaryRecord],
    fallback: AiSummaryRecord,
) -> AiSummaryRecord:
    if current.source_summary_id is None:
        return fallback
    return next(
        (row for row in lineage if row.summary_id == current.source_summary_id),
        fallback,
    )


def _summary_order(summary: AiSummaryRecord) -> tuple[int, object, int]:
    return (summary.attempt_no, summary.generated_at, summary.summary_id)


__all__ = [
    'calculate_retry_counts',
    'is_successful_summary',
    'has_unresolved_key_points',
    'is_retry_resolved',
    'resolve_effective_summaries',
    'select_retry_targets',
]
