"""Track which logical chip configurations belong to the current fleet.

Revision ID: 0009_account_fleet_membership
Revises: 0008_card_link_visibility
"""

import sqlalchemy as sa

from alembic import op

revision = "0009_account_fleet_membership"
down_revision = "0008_card_link_visibility"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "accounts",
        sa.Column("in_fleet", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.create_index("ix_accounts_in_fleet", "accounts", ["in_fleet"])
    op.alter_column("accounts", "in_fleet", server_default=None)


def downgrade() -> None:
    op.drop_index("ix_accounts_in_fleet", table_name="accounts")
    op.drop_column("accounts", "in_fleet")
