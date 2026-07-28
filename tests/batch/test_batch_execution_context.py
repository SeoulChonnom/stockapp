from __future__ import annotations

from datetime import date

from app.batch.models import BatchExecutionContext


def test_ai_counts_survive_checkpoint_round_trip() -> None:
    context = BatchExecutionContext(
        job_id=1001,
        business_date=date(2026, 3, 17),
        force_run=False,
        rebuild_page_only=False,
        ai_target_count=6,
        ai_attempted_count=6,
        ai_success_count=4,
        ai_fallback_count=1,
        ai_failed_count=1,
    )

    restored = BatchExecutionContext.from_checkpoint(
        context.to_checkpoint(),
        job_id=context.job_id,
        business_date=context.business_date,
        force_run=context.force_run,
        rebuild_page_only=context.rebuild_page_only,
    )

    assert restored.ai_target_count == 6
    assert restored.ai_attempted_count == 6
    assert restored.ai_success_count == 4
    assert restored.ai_fallback_count == 1
    assert restored.ai_failed_count == 1
