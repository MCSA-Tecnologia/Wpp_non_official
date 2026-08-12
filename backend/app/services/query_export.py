from __future__ import annotations

import io

import pandas as pd

from ..config import get_settings


EXPECTED_COLUMNS = ["pessoaId", "Nome", "email", "Telefone", "observacao", "Credor", "Campanha"]


def export_contacts_xlsx() -> bytes:
    settings = get_settings()
    required = (
        settings.source_sql_server,
        settings.source_sql_database,
        settings.source_sql_username,
        settings.source_sql_password,
    )
    if not all(required):
        raise RuntimeError("A conexão SQL Server de origem ainda não foi configurada.")
    query_path = settings.source_query_path
    if not query_path.exists():
        raise RuntimeError(f"Arquivo de query não encontrado: {query_path}")
    query = query_path.read_text(encoding="utf-8").strip()
    if not query.lower().startswith(("select", "with")):
        raise RuntimeError("A query configurada precisa ser somente leitura.")

    try:
        import pyodbc
    except ImportError as exc:
        raise RuntimeError("Instale o extra sqlserver para habilitar a exportação.") from exc

    connection_string = (
        f"DRIVER={{{settings.source_sql_driver}}};SERVER={settings.source_sql_server};"
        f"DATABASE={settings.source_sql_database};UID={settings.source_sql_username};"
        f"PWD={settings.source_sql_password};TrustServerCertificate=yes;"
    )
    with pyodbc.connect(connection_string, timeout=settings.source_query_timeout_seconds) as conn:
        frame = pd.read_sql_query(query, conn)
    frame = frame.head(settings.source_query_max_rows)
    for column in EXPECTED_COLUMNS:
        if column not in frame.columns:
            frame[column] = ""
    leading = EXPECTED_COLUMNS + [
        column for column in frame.columns if column not in EXPECTED_COLUMNS
    ]
    output = io.BytesIO()
    frame[leading].to_excel(output, index=False, engine="openpyxl")
    return output.getvalue()
