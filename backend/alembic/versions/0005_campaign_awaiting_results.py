"""Allow the campaign receipt-wait state.

Revision ID: 0005_campaign_awaiting_results
Revises: 0004_real_only_per_chip_cap
"""

import sqlalchemy as sa
from alembic import op


revision = "0005_campaign_awaiting_results"
down_revision = "0004_real_only_per_chip_cap"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The original enum was VARCHAR(9), based on "cancelled".
    op.alter_column(
        "campaigns",
        "state",
        existing_type=sa.String(length=9),
        type_=sa.String(length=32),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.execute(
        "UPDATE campaigns SET state = 'completed', "
        "finished_at = COALESCE(finished_at, CURRENT_TIMESTAMP) "
        "WHERE state = 'awaiting_results'"
    )
    op.alter_column(
        "campaigns",
        "state",
        existing_type=sa.String(length=32),
        type_=sa.String(length=9),
        existing_nullable=False,
    )
