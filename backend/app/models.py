from __future__ import annotations

import enum
import uuid
from datetime import date, datetime, timezone

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Role(str, enum.Enum):
    admin = "admin"
    operator = "operator"


class AccountState(str, enum.Enum):
    offline = "offline"
    connecting = "connecting"
    qr_required = "qr_required"
    ready = "ready"
    degraded = "degraded"
    backoff = "backoff"
    logged_out = "logged_out"
    disabled = "disabled"


class CampaignState(str, enum.Enum):
    draft = "draft"
    scheduled = "scheduled"
    active = "active"
    awaiting_results = "awaiting_results"
    paused = "paused"
    completed = "completed"
    cancelled = "cancelled"


class JobState(str, enum.Enum):
    pending = "pending"
    scheduled = "scheduled"
    leased = "leased"
    sending = "sending"
    sent = "sent"
    delivered = "delivered"
    failed = "failed"
    review_required = "review_required"
    cancelled = "cancelled"


class ImportState(str, enum.Enum):
    processing = "processing"
    ready = "ready"
    consumed = "consumed"
    failed = "failed"


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[Role] = mapped_column(Enum(Role, native_enum=False), default=Role.operator)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    external_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(120))
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    state: Mapped[AccountState] = mapped_column(
        Enum(AccountState, native_enum=False), default=AccountState.disabled, index=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    in_fleet: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    node_id: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    lease_owner: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    qr_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    sent_today: Mapped[int] = mapped_column(Integer, default=0)
    sent_today_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    reconnect_count: Mapped[int] = mapped_column(Integer, default=0)
    session_revision: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class AccountAuthRecord(Base):
    __tablename__ = "account_auth_records"
    __table_args__ = (UniqueConstraint("account_id", "category", "key_id"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"))
    category: Mapped[str] = mapped_column(String(80))
    key_id: Mapped[str] = mapped_column(String(255))
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class ImportBatch(Base):
    __tablename__ = "import_batches"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    filename: Mapped[str] = mapped_column(String(255))
    state: Mapped[ImportState] = mapped_column(
        Enum(ImportState, native_enum=False), default=ImportState.processing
    )
    total_rows: Mapped[int] = mapped_column(Integer, default=0)
    valid_rows: Mapped[int] = mapped_column(Integer, default=0)
    invalid_rows: Mapped[int] = mapped_column(Integer, default=0)
    duplicate_rows: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    rows: Mapped[list["ImportRow"]] = relationship(
        back_populates="batch", cascade="all, delete-orphan"
    )


class ImportRow(Base):
    __tablename__ = "import_rows"
    __table_args__ = (Index("ix_import_rows_batch_valid", "batch_id", "valid"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    batch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("import_batches.id", ondelete="CASCADE"))
    row_number: Mapped[int] = mapped_column(Integer)
    raw_data: Mapped[dict] = mapped_column(JSON)
    normalized_phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    valid: Mapped[bool] = mapped_column(Boolean, default=False)
    duplicate: Mapped[bool] = mapped_column(Boolean, default=False)
    validation_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    batch: Mapped[ImportBatch] = relationship(back_populates="rows")


class Campaign(Base):
    __tablename__ = "campaigns"
    __table_args__ = (
        Index(
            "uq_campaign_single_active",
            "state",
            unique=True,
            postgresql_where=text("state = 'active'"),
            sqlite_where=text("state = 'active'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(180))
    state: Mapped[CampaignState] = mapped_column(
        Enum(CampaignState, native_enum=False), default=CampaignState.draft, index=True
    )
    source_import_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("import_batches.id"), nullable=True
    )
    interval_mean_minutes: Mapped[float] = mapped_column(Float, default=0.0)
    effective_interval_minutes: Mapped[float | None] = mapped_column(Float, nullable=True)
    business_start_hour: Mapped[int] = mapped_column(Integer, default=9)
    business_end_hour: Mapped[int] = mapped_column(Integer, default=18)
    timezone: Mapped[str] = mapped_column(String(64), default="America/Sao_Paulo")
    per_chip_daily_cap_snapshot: Mapped[int] = mapped_column(Integer)
    estimated_start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    estimated_finish_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class MessageCardAsset(Base):
    __tablename__ = "message_card_assets"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(80), default="image/jpeg")
    content: Mapped[bytes] = mapped_column(LargeBinary)
    byte_size: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class MessageVariant(Base):
    __tablename__ = "message_variants"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    campaign_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"))
    body: Mapped[str] = mapped_column(Text)
    card_text: Mapped[str] = mapped_column(String(120), default="")
    card_url: Mapped[str] = mapped_column(Text, default="")
    card_asset_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("message_card_assets.id"), nullable=True
    )
    card_show_url: Mapped[bool] = mapped_column(Boolean, default=True)
    weight: Mapped[int] = mapped_column(Integer, default=100)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Contact(Base):
    __tablename__ = "contacts"
    __table_args__ = (UniqueConstraint("campaign_id", "phone"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    campaign_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"))
    phone: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str] = mapped_column(String(255), default="")
    pessoa_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    email: Mapped[str] = mapped_column(String(320), default="")
    observacao: Mapped[str] = mapped_column(Text, default="")
    credor: Mapped[str] = mapped_column(String(255), default="")
    campanha: Mapped[str] = mapped_column(String(255), default="")
    extra_data: Mapped[dict] = mapped_column(JSON, default=dict)


class MessageJob(Base):
    __tablename__ = "message_jobs"
    __table_args__ = (
        Index("ix_jobs_claim", "account_id", "state", "scheduled_at"),
        Index("ix_jobs_campaign_state", "campaign_id", "state"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    idempotency_key: Mapped[str] = mapped_column(String(120), unique=True)
    campaign_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"))
    contact_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("contacts.id", ondelete="CASCADE"))
    variant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("message_variants.id"))
    account_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("accounts.id"), nullable=True)
    state: Mapped[JobState] = mapped_column(
        Enum(JobState, native_enum=False), default=JobState.pending, index=True
    )
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_token: Mapped[str | None] = mapped_column(String(120), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    provider_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)


class MessageAttempt(Base):
    __tablename__ = "message_attempts"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("message_jobs.id", ondelete="CASCADE"))
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id"))
    lease_token: Mapped[str] = mapped_column(String(120))
    state: Mapped[str] = mapped_column(String(40))
    provider_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DeliveryEvent(Base):
    __tablename__ = "delivery_events"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("message_jobs.id", ondelete="CASCADE"))
    provider_message_id: Mapped[str] = mapped_column(String(255), index=True)
    ack_level: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RORegistration(Base):
    __tablename__ = "ro_registrations"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("message_jobs.id", ondelete="CASCADE"), unique=True
    )
    state: Mapped[str] = mapped_column(String(40), default="pending", index=True)
    batch_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    registered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)
    updated_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(120), index=True)
    entity_type: Mapped[str] = mapped_column(String(80))
    entity_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
