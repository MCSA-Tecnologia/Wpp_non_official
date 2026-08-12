from __future__ import annotations

import io
import math
import re
import unicodedata
from typing import Any

import pandas as pd
from fastapi import UploadFile
from sqlalchemy.orm import Session

from ..models import ImportBatch, ImportRow, ImportState, User


COLUMNS = {
    "phone": ("telefone", "phone", "celular", "fone"),
    "name": ("nome", "cliente", "name"),
    "pessoa_id": ("pessoaid", "pessoas_id", "moinadimplentesid"),
    "email": ("email", "e-mail"),
    "observacao": ("observacao", "obs"),
    "credor": ("credor",),
    "campanha": ("campanha",),
}


def _norm_column(value: Any) -> str:
    text = "".join(
        ch for ch in unicodedata.normalize("NFD", str(value)) if unicodedata.category(ch) != "Mn"
    )
    return text.strip().lower().replace(" ", "")


def _clean(value: Any) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ""
    return str(value).strip()


def normalize_phone(value: Any, country_code: str = "55") -> str | None:
    digits = re.sub(r"\D", "", _clean(value)).lstrip("0")
    if digits.startswith(country_code) and len(digits) >= len(country_code) + 10:
        return f"+{digits}"
    if len(digits) in (10, 11):
        return f"+{country_code}{digits}"
    return None


def parse_upload(filename: str, content: bytes) -> pd.DataFrame:
    suffix = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    if suffix in ("xlsx", "xls"):
        return pd.read_excel(io.BytesIO(content), dtype=str)
    if suffix != "csv":
        raise ValueError("Use um arquivo CSV, XLSX ou XLS.")
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return pd.read_csv(
                io.BytesIO(content), dtype=str, sep=None, engine="python", encoding=encoding
            )
        except UnicodeDecodeError:
            continue
    raise ValueError("Não foi possível identificar a codificação do CSV.")


def map_columns(df: pd.DataFrame) -> dict[str, str | None]:
    available = {_norm_column(column): str(column) for column in df.columns}
    result: dict[str, str | None] = {}
    for target, candidates in COLUMNS.items():
        result[target] = next((available[c] for c in candidates if c in available), None)
    return result


def persist_import(
    db: Session, *, filename: str, content: bytes, actor: User, country_code: str = "55"
) -> ImportBatch:
    batch = ImportBatch(filename=filename, created_by_id=actor.id, state=ImportState.processing)
    db.add(batch)
    db.flush()
    try:
        df = parse_upload(filename, content)
        columns = map_columns(df)
        if not columns["phone"]:
            raise ValueError("O arquivo precisa ter a coluna Telefone.")
        missing_columns = [
            label
            for key, label in (
                ("pessoa_id", "pessoaId"),
                ("credor", "Credor"),
                ("campanha", "Campanha"),
            )
            if not columns[key]
        ]
        if missing_columns:
            raise ValueError(f"Colunas obrigatórias ausentes: {', '.join(missing_columns)}.")

        seen: set[str] = set()
        valid_count = invalid_count = duplicate_count = 0
        rows: list[ImportRow] = []
        for index, source in enumerate(df.to_dict(orient="records"), start=2):
            raw = {str(key): _clean(value) for key, value in source.items()}
            phone = normalize_phone(source.get(columns["phone"]), country_code)
            duplicate = bool(phone and phone in seen)
            missing_values = [
                label
                for key, label in (
                    ("pessoa_id", "pessoaId"),
                    ("credor", "Credor"),
                    ("campanha", "Campanha"),
                )
                if not _clean(source.get(columns[key]))
            ]
            error = None
            if not phone:
                error = "Telefone inválido"
                invalid_count += 1
            elif duplicate:
                error = "Telefone duplicado no arquivo"
                duplicate_count += 1
            elif missing_values:
                error = f"Campos obrigatórios vazios: {', '.join(missing_values)}"
                invalid_count += 1
            else:
                seen.add(phone)
                valid_count += 1
            rows.append(
                ImportRow(
                    batch_id=batch.id,
                    row_number=index,
                    raw_data=raw,
                    normalized_phone=phone,
                    valid=error is None,
                    duplicate=duplicate,
                    validation_error=error,
                )
            )
        db.add_all(rows)
        batch.total_rows = len(rows)
        batch.valid_rows = valid_count
        batch.invalid_rows = invalid_count
        batch.duplicate_rows = duplicate_count
        batch.state = ImportState.ready
    except Exception as exc:
        batch.state = ImportState.failed
        batch.error = str(exc)
        db.commit()
        raise
    db.commit()
    db.refresh(batch)
    return batch


async def read_upload(file: UploadFile, max_bytes: int = 20 * 1024 * 1024) -> bytes:
    content = await file.read(max_bytes + 1)
    if len(content) > max_bytes:
        raise ValueError("Arquivo maior que 20 MB.")
    return content


def canonical_row(raw: dict[str, Any], columns: dict[str, str | None]) -> dict[str, str]:
    return {
        field: _clean(raw.get(column)) if column else ""
        for field, column in columns.items()
        if field != "phone"
    }
