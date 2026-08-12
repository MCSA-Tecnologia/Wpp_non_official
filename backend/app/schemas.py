from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from .models import AccountState, CampaignState, JobState, Role


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class LoginRequest(BaseModel):
    email: str
    password: str


class UserOut(ORMModel):
    id: uuid.UUID
    email: str
    role: Role


class UserCreate(BaseModel):
    email: str
    password: str = Field(min_length=10)
    role: Role = Role.operator


class PasswordChange(BaseModel):
    current_password: str
    new_password: str = Field(min_length=12)


class AccountCreate(BaseModel):
    external_id: str = Field(min_length=1, max_length=64)
    display_name: str = Field(min_length=1, max_length=120)


class AccountBulkCreate(BaseModel):
    count: int = Field(ge=1, le=30)
    prefix: str = "chip"


class AccountOut(ORMModel):
    id: uuid.UUID
    external_id: str
    display_name: str
    phone: str | None
    state: AccountState
    enabled: bool
    node_id: str | None
    last_heartbeat_at: datetime | None
    last_error: str | None
    qr_code: str | None
    sent_today: int
    reconnect_count: int


class ImportRowOut(ORMModel):
    id: uuid.UUID
    row_number: int
    raw_data: dict[str, Any]
    normalized_phone: str | None
    valid: bool
    duplicate: bool
    validation_error: str | None


class ImportBatchOut(ORMModel):
    id: uuid.UUID
    filename: str
    state: str
    total_rows: int
    valid_rows: int
    invalid_rows: int
    duplicate_rows: int
    error: str | None
    created_at: datetime
    preview: list[ImportRowOut] = []


class EstimateRequest(BaseModel):
    import_id: uuid.UUID
    interval_mean_minutes: float = Field(ge=0, le=30)


class EstimateOut(BaseModel):
    valid_contacts: int
    healthy_accounts: int
    contacts_per_account: dict[str, int]
    effective_interval_minutes: float
    estimated_start_at: datetime | None
    estimated_finish_at: datetime | None
    duration_minutes: float
    spills_to_next_day: bool
    per_chip_daily_cap: int
    daily_cap: int
    daily_capacity: int
    remaining_capacity_today: int
    warnings: list[str]


class CampaignCreate(BaseModel):
    name: str = Field(min_length=1, max_length=180)
    import_id: uuid.UUID
    message: str = Field(min_length=1)
    card_revision: str = Field(default="", max_length=64)
    interval_mean_minutes: float = Field(ge=0, le=30)
    confirmed_real_send: bool = False


class CampaignOut(ORMModel):
    id: uuid.UUID
    name: str
    state: CampaignState
    interval_mean_minutes: float
    effective_interval_minutes: float | None
    per_chip_daily_cap_snapshot: int
    estimated_start_at: datetime | None
    estimated_finish_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    total: int = 0
    sent: int = 0
    delivered: int = 0
    failed: int = 0
    review_required: int = 0


class ReviewOut(BaseModel):
    id: uuid.UUID
    campaign_id: uuid.UUID
    phone: str
    account: str | None
    started_at: datetime | None
    last_error: str | None
    state: JobState


class ReviewDecision(BaseModel):
    action: str = Field(pattern="^(retry|cancel)$")


class SettingUpdate(BaseModel):
    per_chip_daily_cap: int = Field(
        ge=1,
        le=10000,
        validation_alias=AliasChoices("per_chip_daily_cap", "daily_cap"),
    )
    business_start_hour: int = Field(ge=0, le=23)
    business_end_hour: int = Field(ge=1, le=24)
    timezone: str = "America/Sao_Paulo"


class MessageCardOut(BaseModel):
    text: str = ""
    url: str = ""
    image_asset_id: uuid.UUID | None = None
    image_url: str | None = None
    revision: str = ""
    configured: bool = False
    updated_at: datetime | None = None


class WorkerClaimRequest(BaseModel):
    worker_id: str
    node_id: str
    capacity: int = Field(ge=1, le=30)


class WorkerHeartbeat(BaseModel):
    worker_id: str
    node_id: str
    state: AccountState
    phone: str | None = None
    error: str | None = None
    qr_code: str | None = None


class ClaimedJob(BaseModel):
    id: uuid.UUID
    lease_token: str
    phone: str
    message: str
    card_text: str
    card_url: str
    card_asset_id: uuid.UUID
    contact_name: str


class JobResult(BaseModel):
    lease_token: str
    state: str = Field(pattern="^(sending|sent|failed)$")
    provider_message_id: str | None = None
    error: str | None = None


class AckEvent(BaseModel):
    provider_message_id: str
    ack_level: int = Field(ge=0, le=4)
    payload: dict[str, Any] = {}


class AuthRecordPayload(BaseModel):
    category: str
    key_id: str
    value: Any


class AuthBulkPayload(BaseModel):
    records: list[AuthRecordPayload]
