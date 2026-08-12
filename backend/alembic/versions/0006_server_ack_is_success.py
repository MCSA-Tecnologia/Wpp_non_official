"""Classify WhatsApp server acknowledgements as successful sends.

Revision ID: 0006_server_ack_is_success
Revises: 0005_campaign_awaiting_results
"""

from alembic import op


revision = "0006_server_ack_is_success"
down_revision = "0005_campaign_awaiting_results"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "UPDATE message_jobs AS job SET state = 'sent', last_error = NULL "
        "WHERE job.state = 'review_required' AND EXISTS ("
        "SELECT 1 FROM delivery_events AS event "
        "WHERE event.job_id = job.id AND event.ack_level >= 2)"
    )


def downgrade() -> None:
    # A server-accepted send remains truthful and must not be made uncertain again.
    pass
