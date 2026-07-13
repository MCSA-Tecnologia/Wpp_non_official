"""
Contact queue builder.

Responsibilities:
- Load contacts from CSV / XLSX / SQL Server / test dataframe.
- Normalize Brazilian phone numbers to +55DDDNXXXXXXXX.
- Deduplicate against the previous run (contacts.json.prev) for the same day.
- Apply the message template (NOME_DO_CLIENTE placeholder).
- Distribute contacts round-robin across the authenticated accounts.
"""
from __future__ import annotations

import json
import re
import shutil
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import pandas as pd

import settings

CONTACTS_FILE = Path("contacts.json")
CONTACTS_BACKUP_FILE = Path("contacts.json.prev")

NAME_PLACEHOLDER_RE = re.compile(r"NOME\s*_?\s*DO\s*_?\s*CLIENTE", re.IGNORECASE)

# Columns we recognise (lowercase, accent-free)
PHONE_COLUMNS = ("telefone", "phone", "celular", "fone")
NAME_COLUMNS = ("nome", "cliente", "name")
PESSOA_COLUMNS = ("pessoaid", "pessoas_id", "moinadimplentesid")
EMAIL_COLUMNS = ("email", "e-mail")
OBS_COLUMNS = ("observacao", "obs")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _strip_accents(text: str) -> str:
    return "".join(
        ch for ch in unicodedata.normalize("NFD", str(text)) if unicodedata.category(ch) != "Mn"
    )


def _norm_col(col: str) -> str:
    return _strip_accents(col).strip().lower().replace(" ", "")


def _find_column(df: pd.DataFrame, candidates: tuple[str, ...]) -> Optional[str]:
    mapping = {_norm_col(c): c for c in df.columns}
    for candidate in candidates:
        if candidate in mapping:
            return mapping[candidate]
    return None


def normalize_phone(raw: Any, country_code: str | None = None) -> Optional[str]:
    """
    Normalize any phone-ish input to +<cc><ddd><number>.
    Returns None when the number cannot be a valid mobile.
    """
    cc = country_code or settings.DEFAULT_COUNTRY_CODE
    digits = re.sub(r"\D", "", str(raw or ""))
    if not digits:
        return None

    digits = digits.lstrip("0")

    # Already has country code
    if digits.startswith(cc) and len(digits) >= len(cc) + 10:
        return f"+{digits}"

    # DDD + number (10 = landline/old, 11 = mobile with 9th digit)
    if len(digits) in (10, 11):
        return f"+{cc}{digits}"

    # Number without DDD -> cannot be dialed reliably
    return None


def first_name(full_name: Any) -> str:
    name = str(full_name or "").strip()
    if not name:
        return ""
    return name.split()[0].capitalize()


def apply_template(message: str, name: Any) -> str:
    replacement = first_name(name) or "cliente"
    return NAME_PLACEHOLDER_RE.sub(replacement, message)


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------

def load_file(path: str | Path) -> pd.DataFrame:
    """Load a CSV or XLSX contact file into a DataFrame."""
    target = Path(path)
    suffix = target.suffix.lower()

    if suffix in (".xlsx", ".xls"):
        return pd.read_excel(target, dtype=str)

    # CSV: try common encodings and separators
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return pd.read_csv(target, dtype=str, sep=None, engine="python", encoding=encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError(f"Não foi possível ler o arquivo {target} (encoding não reconhecido).")


def get_connection():
    """Open a SQL Server connection (lazy pyodbc import, optional dependency)."""
    if not settings.SERVER:
        raise RuntimeError(
            "Banco não configurado (.env sem SERVER/DATABASE). "
            "Use um arquivo CSV/XLSX ou configure o banco."
        )
    import pyodbc  # lazy import so the project runs without ODBC installed

    conn_str = (
        f"DRIVER={{{settings.ODBC_DRIVER}}};"
        f"SERVER={settings.SERVER};DATABASE={settings.DATABASE};"
        f"UID={settings.USERNAME};PWD={settings.PASSWORD};TrustServerCertificate=yes;"
    )
    return pyodbc.connect(conn_str, timeout=30)


def load_database() -> pd.DataFrame:
    """Load contacts from SQL Server using QUERY_CLIENTS_PHONE."""
    with get_connection() as conn:
        return pd.read_sql(settings.QUERY_CLIENTS_PHONE, conn)


def fetch_credor_campanha() -> dict[str, list[str]]:
    """
    Fetch distinct CREDOR/CAMPANHA pairs from the database and return a
    mapping {credor: [campanha, ...]} used to fill the frontend dropdowns.
    """
    with get_connection() as conn:
        df = pd.read_sql(settings.QUERY_CREDOR_CAMPANHA, conn)

    mapping: dict[str, list[str]] = {}
    if df.empty:
        return mapping

    df.columns = [str(c).strip().upper() for c in df.columns]
    if "CREDOR" not in df.columns or "CAMPANHA" not in df.columns:
        raise ValueError("A query de credores precisa retornar as colunas CREDOR e CAMPANHA.")

    df["CREDOR"] = df["CREDOR"].astype(str).str.strip()
    df["CAMPANHA"] = df["CAMPANHA"].astype(str).str.strip()
    df = df[(df["CREDOR"] != "") & (df["CAMPANHA"] != "")]
    for credor, group in df.groupby("CREDOR", sort=True):
        mapping[credor] = sorted(group["CAMPANHA"].drop_duplicates().tolist())
    return mapping


def test_dataframe() -> pd.DataFrame:
    """Tiny dataframe for --test runs."""
    return pd.DataFrame(
        [["Master X", "31 9137-6705", ""]],
        columns=["Nome", "Telefone", "pessoaId"],
    )


# ---------------------------------------------------------------------------
# Queue building
# ---------------------------------------------------------------------------

def _load_previous_sent_today() -> set[str]:
    """Phones already sent successfully today (from the previous queue file)."""
    source = CONTACTS_FILE if CONTACTS_FILE.exists() else CONTACTS_BACKUP_FILE
    if not source.exists():
        return set()
    try:
        previous = json.loads(source.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return set()

    today = datetime.now().strftime("%Y-%m-%d")
    sent_today: set[str] = set()
    for contact in previous:
        sent_at = str(contact.get("sentAt") or "")
        if contact.get("sent") and sent_at.startswith(today):
            sent_today.add(str(contact.get("phone")))
    return sent_today


def build_queue(
    df: pd.DataFrame,
    accounts: list[str],
    message: str | None = None,
    button_url: str | None = None,
) -> dict[str, Any]:
    """
    Turn a raw dataframe into contacts.json entries assigned round-robin.
    Returns a summary dict: {"contacts": [...], "loaded", "invalid", "duplicated", "deduped"}.
    """
    if not accounts:
        raise ValueError("Nenhuma conta autenticada para distribuir os contatos.")

    base_message = message if message not in (None, "") else settings.CONTACT_MESSAGE
    url = button_url if button_url is not None else settings.CONTACT_BUTTON_URL

    phone_col = _find_column(df, PHONE_COLUMNS)
    if not phone_col:
        raise ValueError("O arquivo precisa ter a coluna 'Telefone' (ou 'telefone').")
    name_col = _find_column(df, NAME_COLUMNS)
    pessoa_col = _find_column(df, PESSOA_COLUMNS)
    email_col = _find_column(df, EMAIL_COLUMNS)
    obs_col = _find_column(df, OBS_COLUMNS)

    already_sent_today = _load_previous_sent_today()

    contacts: list[dict[str, Any]] = []
    seen: set[str] = set()
    invalid = duplicated = deduped = 0

    rows = df.to_dict(orient="records")
    for row in rows:
        phone = normalize_phone(row.get(phone_col))
        if not phone:
            invalid += 1
            continue
        if phone in seen:
            duplicated += 1
            continue
        seen.add(phone)
        if phone in already_sent_today:
            deduped += 1
            continue

        name = row.get(name_col) if name_col else ""
        pessoa_id = row.get(pessoa_col) if pessoa_col else None
        if pessoa_id is not None:
            pessoa_id = str(pessoa_id).strip() or None

        contacts.append(
            {
                "phone": phone,
                "name": str(name or "").strip(),
                "message": apply_template(base_message, name),
                "buttonUrl": url or "",
                "sent": False,
                "sentBy": None,
                "sentAt": None,
                "delivered": False,
                "deliveredAt": None,
                "ackLevel": None,
                "error": None,
                "pessoaId": pessoa_id,
                "email": str(row.get(email_col) or "").strip() if email_col else "",
                "observacao": str(row.get(obs_col) or "").strip() if obs_col else "",
                "roRegistered": False,
                "roRegisteredAt": None,
                "roBatchId": None,
                "roStatus": None,
                "roError": None,
            }
        )

    # Round-robin distribution across authenticated accounts
    for index, contact in enumerate(contacts):
        contact["sentBy"] = accounts[index % len(accounts)]

    return {
        "contacts": contacts,
        "loaded": len(rows),
        "invalid": invalid,
        "duplicated": duplicated,
        "deduped": deduped,
    }


def write_queue(contacts: list[dict[str, Any]], path: str | Path = CONTACTS_FILE) -> None:
    """Backup the previous queue and atomically write the new one."""
    target = Path(path)
    if target.exists():
        shutil.copy2(target, CONTACTS_BACKUP_FILE)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(contacts, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(target)
