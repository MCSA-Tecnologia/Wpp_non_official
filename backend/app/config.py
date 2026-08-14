from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"), env_prefix="AUTOWPP_", extra="ignore"
    )

    app_name: str = "AutoWpp Control Plane"
    environment: str = "development"
    api_prefix: str = "/api/v1"
    database_url: str = "postgresql+psycopg://autowpp:autowpp@localhost:5432/autowpp"
    redis_url: str = "redis://localhost:6379/0"
    cors_origins: str = "http://localhost:5173"

    jwt_secret: str = "change-me-before-production"
    jwt_algorithm: str = "HS256"
    access_token_minutes: int = 15
    refresh_token_days: int = 7
    cookie_secure: bool = False
    bootstrap_admin_email: str = "admin@local"
    bootstrap_admin_password: str = "change-me-now"
    worker_token: str = "change-worker-token"
    encryption_key: str = ""

    timezone: str = "America/Sao_Paulo"
    business_start_hour: int = 9
    business_end_hour: int = 18
    safe_min_interval_minutes: float = 0.5
    max_interval_minutes: float = 30.0
    account_degraded_seconds: int = 30
    account_lease_seconds: int = 90
    account_heartbeat_seconds: int = 10
    per_chip_daily_cap: int | None = None
    # The reconciler runs every five seconds. A 115-second grace keeps the final
    # classification inside the operator's two-minute SLA even with loop jitter.
    result_grace_seconds: int = 115

    source_sql_server: str = ""
    source_sql_database: str = ""
    source_sql_username: str = ""
    source_sql_password: str = ""
    source_sql_driver: str = "ODBC Driver 18 for SQL Server"
    source_query_file: str = "app/sql/contact_export.sql"
    source_query_timeout_seconds: int = 30
    source_query_max_rows: int = 10000

    openai_api_key: str = ""
    openai_timeout_seconds: int = 60

    ro_enabled: bool = False
    ro_endpoint: str = ""
    ro_timeout_seconds: int = 60
    ro_batch_size: int = 390
    ro_resumo_id: int = 12
    ro_operador_id: int = 227
    ro_origem: str = "API - Whatsapp Unofficial"
    ro_parceiro: str = "API Whatsapp Unofficial"

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]

    @property
    def source_query_path(self) -> Path:
        path = Path(self.source_query_file)
        if path.is_absolute():
            return path
        return Path(__file__).resolve().parent.parent / path


@lru_cache
def get_settings() -> Settings:
    return Settings()
