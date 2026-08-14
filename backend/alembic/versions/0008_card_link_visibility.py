"""Snapshot whether the card URL is visible in the message text.

Revision ID: 0008_card_link_visibility
Revises: 0007_message_card
"""

import sqlalchemy as sa

from alembic import op

revision = "0008_card_link_visibility"
down_revision = "0007_message_card"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "message_variants",
        sa.Column(
            "card_show_url",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )
    op.alter_column("message_variants", "card_show_url", server_default=None)


def downgrade() -> None:
    op.drop_column("message_variants", "card_show_url")
