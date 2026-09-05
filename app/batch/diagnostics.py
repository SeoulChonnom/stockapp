from __future__ import annotations

from types import MappingProxyType
from typing import Final

from app.batch.models import BatchExecutionContext

AI_DETAIL_ANALYSIS_DEGRADED = 'AI_DETAIL_ANALYSIS_DEGRADED'
AI_SUMMARY_FALLBACK = 'AI_SUMMARY_FALLBACK'
AI_SUMMARY_NO_CLUSTERS = 'AI_SUMMARY_NO_CLUSTERS'
CLUSTER_ENRICHMENT_FALLBACK = 'CLUSTER_ENRICHMENT_FALLBACK'
INDEX_FETCH_FAILED = 'INDEX_FETCH_FAILED'
INDEX_FUTURE_SOURCE_DATE = 'INDEX_FUTURE_SOURCE_DATE'
INDEX_NONE_COLLECTED = 'INDEX_NONE_COLLECTED'
INDEX_STALE_SOURCE_DATE = 'INDEX_STALE_SOURCE_DATE'
NEWS_COLLECT_FAILED = 'NEWS_COLLECT_FAILED'
NEWS_COVERAGE_GAP_SKIPPED = 'NEWS_COVERAGE_GAP_SKIPPED'
NEWS_COVERAGE_INCOMPLETE = 'NEWS_COVERAGE_INCOMPLETE'
NEWS_PAGINATION_CAP = 'NEWS_PAGINATION_CAP'
PARTIAL_UNCATEGORIZED = 'UNCATEGORIZED'
SIMILARITY_GROUPING_FAILED = 'SIMILARITY_GROUPING_FAILED'

SIMILAR_GROUP_FAILURE: Final = MappingProxyType(
    {
        'category': 'SIMILAR_GROUP',
        'code': SIMILARITY_GROUPING_FAILED,
        'message': '유사 기사 묶음을 준비하지 못했습니다.',
    }
)


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


def build_detail_analysis_degradation_log_line(
    context: BatchExecutionContext,
) -> str | None:
    """Report the CLUSTER_DETAIL_ANALYSIS degradation rate as one bounded line.

    A degraded detail analysis is deliberately kept out of ``fallback_count``
    and ``add_partial`` -- one cluster's analysis quality must never flip the
    whole daily page to PARTIAL -- which left the rate with no operational
    signal at all. This line is keyed by issue code from the fixed
    ``ANALYSIS_ISSUE_MESSAGES`` vocabulary, the same way ``build_diagnostic_log_line``
    is keyed by category, so it stays one line no matter how many clusters
    degrade in a run.
    """
    if not context.ai_detail_analysis_degraded_count:
        return None
    counts = {
        code: count
        for code, count in context.detail_analysis_issue_counts.items()
        if count > 0
    }
    if not counts:
        counts = {PARTIAL_UNCATEGORIZED: context.ai_detail_analysis_degraded_count}
    ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    body = ', '.join(f'{code} x{count}' for code, count in ordered)
    return (
        f'{AI_DETAIL_ANALYSIS_DEGRADED}: '
        f'{context.ai_detail_analysis_degraded_count} cluster detail analysis '
        f'row(s) persisted degraded ({body}).'
    )


def build_attempt_log_line(context: BatchExecutionContext) -> str | None:
    """Report that a job needed more than one attempt to reach its result.

    A restart leaves no trace in the PARTIAL categories -- a job can burn
    attempts and still finish SUCCESS -- so without this the only evidence is
    step-run rows nobody reads. It stays out of ``partial_reasons`` on purpose:
    those are rendered as public page issues, and a retry is an operational
    fact rather than something wrong with the day's content.
    """
    if context.attempt_count <= 1:
        return None
    line = f'Job ran on attempt {context.attempt_count}'
    if context.llm_retry_count > 0:
        line += f' after {context.llm_retry_count} transient LLM retry(s)'
    return f'{line}.'


def build_log_summary(context: BatchExecutionContext) -> str | None:
    """Join the step log with the degradation summary, without mutating context.

    Both the normal finalize path and the failure-marking path in the
    orchestrator go through here so a degraded job reports the same summary
    whichever way it ends.
    """
    attempt_log_line = build_attempt_log_line(context)
    diagnostic_log_line = build_diagnostic_log_line(context)
    detail_analysis_log_line = build_detail_analysis_degradation_log_line(context)
    messages = [
        *context.log_messages,
        *([attempt_log_line] if attempt_log_line is not None else []),
        *([diagnostic_log_line] if diagnostic_log_line is not None else []),
        *([detail_analysis_log_line] if detail_analysis_log_line is not None else []),
    ]
    return ' '.join(messages) or None


__all__ = [
    'AI_DETAIL_ANALYSIS_DEGRADED',
    'AI_SUMMARY_FALLBACK',
    'AI_SUMMARY_NO_CLUSTERS',
    'CLUSTER_ENRICHMENT_FALLBACK',
    'INDEX_FETCH_FAILED',
    'INDEX_FUTURE_SOURCE_DATE',
    'INDEX_NONE_COLLECTED',
    'INDEX_STALE_SOURCE_DATE',
    'NEWS_COLLECT_FAILED',
    'NEWS_COVERAGE_GAP_SKIPPED',
    'NEWS_COVERAGE_INCOMPLETE',
    'NEWS_PAGINATION_CAP',
    'PARTIAL_UNCATEGORIZED',
    'SIMILARITY_GROUPING_FAILED',
    'SIMILAR_GROUP_FAILURE',
    'build_attempt_log_line',
    'build_detail_analysis_degradation_log_line',
    'build_diagnostic_log_line',
    'build_log_summary',
]
