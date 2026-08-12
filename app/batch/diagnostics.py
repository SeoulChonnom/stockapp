from __future__ import annotations

from app.batch.models import BatchExecutionContext

AI_SUMMARY_FALLBACK = 'AI_SUMMARY_FALLBACK'
AI_SUMMARY_NO_CLUSTERS = 'AI_SUMMARY_NO_CLUSTERS'
CLUSTER_ENRICHMENT_FALLBACK = 'CLUSTER_ENRICHMENT_FALLBACK'
INDEX_FETCH_FAILED = 'INDEX_FETCH_FAILED'
INDEX_FUTURE_SOURCE_DATE = 'INDEX_FUTURE_SOURCE_DATE'
INDEX_NONE_COLLECTED = 'INDEX_NONE_COLLECTED'
INDEX_STALE_SOURCE_DATE = 'INDEX_STALE_SOURCE_DATE'
NEWS_COLLECT_FAILED = 'NEWS_COLLECT_FAILED'
NEWS_COVERAGE_INCOMPLETE = 'NEWS_COVERAGE_INCOMPLETE'
NEWS_PAGINATION_CAP = 'NEWS_PAGINATION_CAP'
PARTIAL_UNCATEGORIZED = 'UNCATEGORIZED'


def build_diagnostic_log_line(context: BatchExecutionContext) -> str | None:
    """Summarize why a job degraded, as one bounded line for the log summary.

    The line is keyed by category rather than by individual reason so its length
    stays constant no matter how many targets degraded. A job that carries a
    PARTIAL signal without any category still reports ``UNCATEGORIZED`` -- a
    degraded job must never finish with a silent log summary.
    """
    counts = {
        category: count
        for category, count in context.partial_categories.items()
        if count > 0
    }
    if not counts:
        residual = (
            len(context.partial_reasons)
            + len(context.warning_messages)
            + context.fallback_count
        )
        if not residual and not context.partial_message:
            return None
        counts = {PARTIAL_UNCATEGORIZED: residual or 1}
    ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    body = ', '.join(f'{category} x{count}' for category, count in ordered)
    return f'PARTIAL diagnostics: {body}.'


def build_log_summary(context: BatchExecutionContext) -> str | None:
    """Join the step log with the degradation summary, without mutating context.

    Both the normal finalize path and the failure-marking path in the
    orchestrator go through here so a degraded job reports the same summary
    whichever way it ends.
    """
    diagnostic_log_line = build_diagnostic_log_line(context)
    messages = [
        *context.log_messages,
        *([diagnostic_log_line] if diagnostic_log_line is not None else []),
    ]
    return ' '.join(messages) or None


__all__ = [
    'AI_SUMMARY_FALLBACK',
    'AI_SUMMARY_NO_CLUSTERS',
    'CLUSTER_ENRICHMENT_FALLBACK',
    'INDEX_FETCH_FAILED',
    'INDEX_FUTURE_SOURCE_DATE',
    'INDEX_NONE_COLLECTED',
    'INDEX_STALE_SOURCE_DATE',
    'NEWS_COLLECT_FAILED',
    'NEWS_COVERAGE_INCOMPLETE',
    'NEWS_PAGINATION_CAP',
    'PARTIAL_UNCATEGORIZED',
    'build_diagnostic_log_line',
    'build_log_summary',
]
