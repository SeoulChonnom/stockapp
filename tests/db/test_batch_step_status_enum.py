from __future__ import annotations

from app.db.enums import BatchStepStatus


def test_batch_step_status_members_match_database_enum():
    assert [status.value for status in BatchStepStatus] == [
        'RUNNING',
        'SUCCEEDED',
        'FAILED',
    ]


def test_batch_step_status_is_string_comparable():
    assert BatchStepStatus.SUCCEEDED == 'SUCCEEDED'
