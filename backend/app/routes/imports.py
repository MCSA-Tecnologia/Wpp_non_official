from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..dependencies import get_current_user
from ..models import ImportBatch, ImportRow, User
from ..schemas import ImportBatchOut, ImportRowOut
from ..services.imports import persist_import, read_upload


router = APIRouter(prefix="/imports", tags=["imports"])


def serialize_batch(db: Session, batch: ImportBatch, preview_limit: int = 100) -> ImportBatchOut:
    rows = list(
        db.scalars(
            select(ImportRow)
            .where(ImportRow.batch_id == batch.id)
            .order_by(ImportRow.row_number)
            .limit(preview_limit)
        )
    )
    payload = ImportBatchOut.model_validate(batch)
    payload.preview = [ImportRowOut.model_validate(row) for row in rows]
    return payload


@router.post("/preview", response_model=ImportBatchOut, status_code=201)
async def upload_preview(
    file: UploadFile = File(...),
    actor: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        content = await read_upload(file)
        batch = persist_import(
            db, filename=file.filename or "contatos.csv", content=content, actor=actor
        )
        return serialize_batch(db, batch)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/{batch_id}", response_model=ImportBatchOut)
def get_import(
    batch_id: uuid.UUID,
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    batch = db.get(ImportBatch, batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="Importação não encontrada")
    return serialize_batch(db, batch)

