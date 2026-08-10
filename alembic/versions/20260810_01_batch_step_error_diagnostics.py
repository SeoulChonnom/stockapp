"""Add diagnostic fields to batch step runs.

Revision ID: 20260810_01_step_errors
Revises: 20260807_01_step_run
Create Date: 2026-08-10
"""

import sqlalchemy as sa
from alembic import op

revision = '20260810_01_step_errors'
down_revision = '20260807_01_step_run'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'batch_job_step_run',
        sa.Column('error_message', sa.Text(), nullable=True),
    )
    op.add_column(
        'batch_job_step_run',
        sa.Column('error_log', sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('batch_job_step_run', 'error_log')
    op.drop_column('batch_job_step_run', 'error_message')
