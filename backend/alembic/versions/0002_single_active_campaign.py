"""Enforce a single active campaign across API replicas.

Revision ID: 0002_single_active_campaign
Revises: 0001_initial
"""

from alembic import op


revision = "0002_single_active_campaign"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_campaign_single_active "
        "ON campaigns (state) WHERE state = 'active'"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_campaign_single_active")
