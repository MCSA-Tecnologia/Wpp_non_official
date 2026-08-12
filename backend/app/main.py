from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from prometheus_client import CONTENT_TYPE_LATEST, Gauge, Histogram, generate_latest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from .config import get_settings
from .database import SessionLocal, get_db
from .dependencies import get_current_user
from .events import broker
from .models import (
    Account,
    AccountState,
    DeliveryEvent,
    JobState,
    MessageAttempt,
    MessageJob,
    Role,
    User,
)
from .routes import accounts, auth, campaigns, imports, internal, operations
from .security import hash_password, validate_runtime_secrets
from .services.campaign_state import reconcile_completed_campaigns
from .services.maintenance import recover_stale_state
from .services.ro import process_pending_ro


logger = logging.getLogger("autowpp")
settings = get_settings()

ACCOUNT_GAUGE = Gauge("autowpp_accounts", "Accounts by state", ["state"])
JOB_GAUGE = Gauge("autowpp_jobs", "Jobs by state", ["state"])
SENT_TODAY_GAUGE = Gauge("autowpp_sent_today", "Messages sent today across the fleet")
RECONNECT_GAUGE = Gauge("autowpp_reconnections", "Accumulated account reconnections")
DELAYED_JOB_GAUGE = Gauge("autowpp_delayed_jobs", "Scheduled jobs past their due time")
ACK_GAUGE = Gauge("autowpp_ack_events", "Persisted WhatsApp acknowledgement events")
ERROR_15M_GAUGE = Gauge("autowpp_errors_15m", "Failed or uncertain attempts in the last 15 minutes")
HTTP_LATENCY = Histogram(
    "autowpp_http_request_duration_seconds",
    "API request latency",
    ["method", "route", "status"],
)


def bootstrap_admin() -> None:
    with SessionLocal() as db:
        if db.scalar(select(User.id).limit(1)):
            return
        db.add(
            User(
                email=settings.bootstrap_admin_email.lower(),
                password_hash=hash_password(settings.bootstrap_admin_password),
                role=Role.admin,
            )
        )
        db.commit()
        logger.warning("Bootstrap admin created; replace the default password immediately.")


def run_maintenance_cycle() -> dict[str, int]:
    with SessionLocal() as db:
        changes = recover_stale_state(db)
        changes["completed"] = reconcile_completed_campaigns(db)
    process_pending_ro()
    return changes


async def maintenance_loop() -> None:
    while True:
        try:
            changes = await asyncio.to_thread(run_maintenance_cycle)
            if any(changes.values()):
                await broker.publish("dashboard", {"type": "maintenance", **changes})
        except Exception:
            logger.exception("Maintenance cycle failed")
        await asyncio.sleep(5)


@asynccontextmanager
async def lifespan(_: FastAPI):
    validate_runtime_secrets()
    await broker.connect()
    bootstrap_admin()
    task = asyncio.create_task(maintenance_loop())
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        await broker.close()


app = FastAPI(
    title=settings.app_name,
    version="3.0.0",
    lifespan=lifespan,
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Content-Type", "X-Worker-Token"],
)


@app.middleware("http")
async def observe_http(request, call_next):
    started = time.perf_counter()
    response = await call_next(request)
    route = getattr(request.scope.get("route"), "path", request.url.path)
    HTTP_LATENCY.labels(request.method, route, str(response.status_code)).observe(
        time.perf_counter() - started
    )
    return response

for route in (auth.router, accounts.router, imports.router, campaigns.router, operations.router):
    app.include_router(route, prefix=settings.api_prefix)
app.include_router(internal.router, prefix=settings.api_prefix)


@app.get("/health/live", tags=["health"])
async def live():
    return {"status": "ok"}


@app.get("/health/ready", tags=["health"])
def ready(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))
    return {"status": "ready"}


@app.get(f"{settings.api_prefix}/events", tags=["events"])
async def events(_: User = Depends(get_current_user)):
    async def stream():
        async for event in broker.subscribe("dashboard"):
            yield f"data: {event}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.get("/metrics", include_in_schema=False)
def metrics(db: Session = Depends(get_db)):
    now = datetime.now(timezone.utc)
    for state in AccountState:
        count = db.scalar(select(func.count(Account.id)).where(Account.state == state)) or 0
        ACCOUNT_GAUGE.labels(state=state.value).set(count)
    for state in JobState:
        count = db.scalar(select(func.count(MessageJob.id)).where(MessageJob.state == state)) or 0
        JOB_GAUGE.labels(state=state.value).set(count)
    SENT_TODAY_GAUGE.set(db.scalar(select(func.coalesce(func.sum(Account.sent_today), 0))) or 0)
    RECONNECT_GAUGE.set(db.scalar(select(func.coalesce(func.sum(Account.reconnect_count), 0))) or 0)
    DELAYED_JOB_GAUGE.set(
        db.scalar(
            select(func.count(MessageJob.id)).where(
                MessageJob.state == JobState.scheduled,
                MessageJob.scheduled_at < now,
            )
        )
        or 0
    )
    ACK_GAUGE.set(db.scalar(select(func.count(DeliveryEvent.id))) or 0)
    ERROR_15M_GAUGE.set(
        db.scalar(
            select(func.count(MessageAttempt.id)).where(
                MessageAttempt.created_at >= now - timedelta(minutes=15),
                MessageAttempt.state.in_([JobState.failed.value, JobState.review_required.value]),
            )
        )
        or 0
    )
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
