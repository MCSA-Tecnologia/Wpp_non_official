"""Make runtime real-only and rename the daily limit to per-chip.

Revision ID: 0004_real_only_per_chip_cap
Revises: 0003_session_revision
"""

import sqlalchemy as sa
from alembic import op


revision = "0004_real_only_per_chip_cap"
down_revision = "0003_session_revision"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("campaigns")}
    if "daily_cap_snapshot" in columns and "per_chip_daily_cap_snapshot" not in columns:
        op.alter_column(
            "campaigns",
            "daily_cap_snapshot",
            new_column_name="per_chip_daily_cap_snapshot",
        )

    settings_table = sa.table(
        "app_settings",
        sa.column("key", sa.String()),
        sa.column("value", sa.JSON()),
    )
    connection = op.get_bind()
    runtime = connection.execute(
        sa.select(settings_table.c.value).where(settings_table.c.key == "runtime")
    ).scalar_one_or_none()
    if runtime and "daily_cap" in runtime and "per_chip_daily_cap" not in runtime:
        migrated = dict(runtime)
        migrated["per_chip_daily_cap"] = migrated.pop("daily_cap")
        connection.execute(
            settings_table.update().where(settings_table.c.key == "runtime").values(value=migrated)
        )


def downgrade() -> None:
    settings_table = sa.table(
        "app_settings",
        sa.column("key", sa.String()),
        sa.column("value", sa.JSON()),
    )
    connection = op.get_bind()
    runtime = connection.execute(
        sa.select(settings_table.c.value).where(settings_table.c.key == "runtime")
    ).scalar_one_or_none()
    if runtime and "per_chip_daily_cap" in runtime and "daily_cap" not in runtime:
        migrated = dict(runtime)
        migrated["daily_cap"] = migrated.pop("per_chip_daily_cap")
        connection.execute(
            settings_table.update().where(settings_table.c.key == "runtime").values(value=migrated)
        )

    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("campaigns")}
    if "per_chip_daily_cap_snapshot" in columns and "daily_cap_snapshot" not in columns:
        op.alter_column(
            "campaigns",
            "per_chip_daily_cap_snapshot",
            new_column_name="daily_cap_snapshot",
        )
