"""Restart workers when an account session is reset.

Revision ID: 0003_session_revision
Revises: 0002_single_active_campaign
"""

import sqlalchemy as sa
from alembic import op


revision = "0003_session_revision"
down_revision = "0002_single_active_campaign"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("accounts")}
    if "session_revision" not in columns:
        op.add_column(
            "accounts",
            sa.Column("session_revision", sa.Integer(), nullable=False, server_default="0"),
        )


def downgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("accounts")}
    if "session_revision" in columns:
        op.drop_column("accounts", "session_revision")
