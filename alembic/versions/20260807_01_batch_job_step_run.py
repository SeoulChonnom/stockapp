"""Add batch_job_step_run to record per-step execution durations.

Revision ID: 20260807_01_step_run
Revises: 20260731_00_baseline
Create Date: 2026-08-07
"""

from alembic import op

revision = '20260807_01_step_run'
down_revision = '20260731_00_baseline'
branch_labels = None
depends_on = None

_UPGRADE_SQL = """
CREATE TYPE batch_step_status_enum AS ENUM ('RUNNING', 'SUCCEEDED', 'FAILED');

CREATE TABLE batch_job_step_run (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    batch_job_id BIGINT NOT NULL REFERENCES batch_job(id) ON DELETE CASCADE,
    step_code TEXT NOT NULL,
    seq INTEGER NOT NULL,
    status batch_step_status_enum NOT NULL DEFAULT 'RUNNING',
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at TIMESTAMPTZ NULL,
    duration_ms INTEGER NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_batch_job_step_run_job_seq UNIQUE (batch_job_id, seq),
    CONSTRAINT chk_batch_job_step_run_seq_positive
        CHECK (seq >= 1),
    CONSTRAINT chk_batch_job_step_run_ended_after_started
        CHECK (ended_at IS NULL OR ended_at >= started_at),
    CONSTRAINT chk_batch_job_step_run_duration_non_negative
        CHECK (duration_ms IS NULL OR duration_ms >= 0)
);

CREATE INDEX idx_batch_job_step_run_job_seq
    ON batch_job_step_run (batch_job_id, seq);
"""

_DOWNGRADE_SQL = """
DROP TABLE IF EXISTS batch_job_step_run;
DROP TYPE IF EXISTS batch_step_status_enum;
"""


def upgrade() -> None:
    op.execute(_UPGRADE_SQL)


def downgrade() -> None:
    op.execute(_DOWNGRADE_SQL)
