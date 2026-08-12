"""Add the global clickable message card and campaign snapshots.

Revision ID: 0007_message_card
Revises: 0006_server_ack_is_success
"""

import sqlalchemy as sa
from alembic import op


revision = "0007_message_card"
down_revision = "0006_server_ack_is_success"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "message_card_assets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("content_type", sa.String(length=80), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_message_card_assets_sha256", "message_card_assets", ["sha256"], unique=False
    )
    op.alter_column("message_variants", "button_url", new_column_name="card_url")
    op.add_column(
        "message_variants",
        sa.Column("card_text", sa.String(length=120), nullable=False, server_default=""),
    )
    op.add_column(
        "message_variants", sa.Column("card_asset_id", sa.Uuid(), nullable=True)
    )
    op.create_foreign_key(
        "fk_message_variants_card_asset_id",
        "message_variants",
        "message_card_assets",
        ["card_asset_id"],
        ["id"],
    )
    op.alter_column("message_variants", "card_text", server_default=None)


def downgrade() -> None:
    op.drop_constraint(
        "fk_message_variants_card_asset_id", "message_variants", type_="foreignkey"
    )
    op.drop_column("message_variants", "card_asset_id")
    op.drop_column("message_variants", "card_text")
    op.alter_column("message_variants", "card_url", new_column_name="button_url")
    op.drop_index("ix_message_card_assets_sha256", table_name="message_card_assets")
    op.drop_table("message_card_assets")
