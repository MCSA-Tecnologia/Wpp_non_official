"""
RO / Calltech registration helper.

Registers successfully-sent WhatsApp contacts in Calltech after a run.
Kept self-contained on purpose so payload tweaks stay in one place.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

import settings

CONTACTS_FILE = Path("contacts.json")


# ---------------------------------------------------------------------------
# Contacts I/O
# ---------------------------------------------------------------------------

def load_contacts(path: str | Path = CONTACTS_FILE) -> list[dict[str, Any]]:
    target = Path(path)
    if not target.exists():
        return []
    with target.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def save_contacts(contacts: list[dict[str, Any]], path: str | Path = CONTACTS_FILE) -> None:
    """Atomic write so the shared file never corrupts."""
    target = Path(path)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(contacts, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(target)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# ---------------------------------------------------------------------------
# Field builders
# ---------------------------------------------------------------------------

def normalize_phone(phone: Any) -> str:
    digits = "".join(ch for ch in str(phone or "") if ch.isdigit())
    return f"+{digits}" if digits else ""


def to_calltech_timestamp(value: Any) -> str:
    """Calltech expects naive local timestamps: YYYY-MM-DDTHH:MM:SS."""
    if not value:
        return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)
    return parsed.strftime("%Y-%m-%dT%H:%M:%S")


def is_successful_send(contact: dict[str, Any]) -> bool:
    sent_at = str(contact.get("sentAt") or "")
    return bool(contact.get("sent")) and bool(sent_at) and not sent_at.startswith("ERROR")


def is_ro_pending(contact: dict[str, Any]) -> bool:
    return is_successful_send(contact) and not bool(contact.get("roRegistered"))


def build_origem(context: dict[str, Any]) -> str:
    return str(context.get("origem") or settings.RO_ORIGEM)[:50]


def extract_codigo_campanha(raw_value: Any) -> str:
    """'000033 - Prime - Extrajudicial' -> '000033'."""
    digits = "".join(ch for ch in str(raw_value or "") if ch.isdigit())
    if not digits:
        fallback = "".join(ch for ch in str(settings.RO_CODIGO_CAMPANHA or "") if ch.isdigit())
        return fallback or "000000"
    return digits


def derive_campanha_id(codigo_campanha: str) -> int:
    """campanhaId = int(codigoCampanha) + 2 (business rule inherited from v1)."""
    return int(codigo_campanha or "0") + 2


def build_historico(context: dict[str, Any], contact: dict[str, Any]) -> str:
    parceiro = str(context.get("parceiro") or settings.RO_PARCEIRO)
    telefone = normalize_phone(contact.get("phone")).replace("+55", "", 1).replace("+", "")
    message = str(contact.get("message") or "")
    button_url = str(contact.get("buttonUrl") or "").strip()
    historico = f"{parceiro} ({telefone}): {message}"
    if button_url:
        historico = f"{historico} {button_url}"
    return historico[:800]


def build_payload_item(context: dict[str, Any], contact: dict[str, Any]) -> dict[str, Any]:
    """Assemble one Calltech registro. Edit here first if the API changes."""
    pessoa_id = contact.get("pessoaId")
    if pessoa_id in (None, ""):
        raise ValueError(f"Contato {contact.get('phone')} sem pessoaId para registrar no RO.")

    codigo_campanha = extract_codigo_campanha(context.get("codigoCampanha"))
    data_inicio = to_calltech_timestamp(contact.get("sentAt"))
    data_fim = to_calltech_timestamp(contact.get("deliveredAt") or contact.get("sentAt"))

    return {
        "resumoId": int(context.get("resumoId", settings.RO_RESUMO_ID)),
        "operadorId": int(context.get("operadorId", settings.RO_OPERADOR_ID)),
        "codigoCampanha": codigo_campanha,
        "campanhaId": derive_campanha_id(codigo_campanha),
        "dataHora": data_inicio,
        "dataInicio": data_inicio,
        "dataFim": data_fim,
        "pessoaId": int(pessoa_id),
        "origem": build_origem(context),
        "historico": build_historico(context, contact),
    }


# ---------------------------------------------------------------------------
# Batch processing
# ---------------------------------------------------------------------------

def chunk_items(items: list[Any], chunk_size: int) -> list[list[Any]]:
    return [items[i: i + chunk_size] for i in range(0, len(items), chunk_size)]


def mark_contacts_success(contacts, selected_indexes, batch_id, registered_at) -> None:
    for index in selected_indexes:
        contacts[index].update(
            roRegistered=True, roRegisteredAt=registered_at,
            roBatchId=batch_id, roStatus="success", roError=None,
        )


def mark_contacts_error(contacts, selected_indexes, error_message) -> None:
    for index in selected_indexes:
        contacts[index].update(roRegistered=False, roStatus="error", roError=error_message[:500])


def send_batch(batch: list[dict[str, Any]], endpoint: str) -> tuple[int, dict[str, Any], str]:
    response = requests.post(endpoint, json={"registros": batch}, timeout=settings.RO_TIMEOUT_SECONDS)
    try:
        response_json = response.json()
    except ValueError:
        response_json = {}
    return response.status_code, response_json, response.text


def build_ro_context(overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    context = {
        "endpoint": settings.RO_CALLTECH_ENDPOINT,
        "resumoId": settings.RO_RESUMO_ID,
        "operadorId": settings.RO_OPERADOR_ID,
        "codigoCampanha": settings.RO_CODIGO_CAMPANHA,
        "campanhaId": settings.RO_CAMPANHA_ID,
        "origem": settings.RO_ORIGEM,
        "parceiro": settings.RO_PARCEIRO,
    }
    if overrides:
        context.update({k: v for k, v in overrides.items() if v not in (None, "")})
    return context


def process_ro_after_run(
    context: dict[str, Any] | None = None,
    contacts_path: str | Path = CONTACTS_FILE,
    trigger_min: int | None = None,
    batch_size: int | None = None,
    run_completed: bool = False,
) -> dict[str, Any]:
    """
    Register eligible sends in Calltech.

    Trigger rule:
    - fire when at least `trigger_min` contacts are eligible;
    - also fire when the run has completed (end-of-run remainder);
    - never resend contacts already marked roRegistered=true.
    """
    merged_context = build_ro_context(context)
    contacts = load_contacts(contacts_path)
    eligible = [(i, c) for i, c in enumerate(contacts) if is_ro_pending(c)]

    effective_trigger = trigger_min if trigger_min is not None else settings.RO_TRIGGER_MIN_COUNT
    effective_batch_size = batch_size if batch_size is not None else settings.RO_BATCH_SIZE

    result = {
        "triggered": False, "eligible": len(eligible), "batches": 0,
        "successes": 0, "errors": 0, "skipped": 0, "messages": [],
    }

    if len(eligible) < effective_trigger and not run_completed:
        result["messages"].append(
            f"RO skipped: {len(eligible)} eligible contacts, minimum is {effective_trigger}."
        )
        return result

    payload_entries: list[tuple[int, dict[str, Any]]] = []
    for index, contact in eligible:
        try:
            payload_entries.append((index, build_payload_item(merged_context, contact)))
        except Exception as exc:
            contacts[index].update(roRegistered=False, roStatus="error", roError=str(exc)[:500])
            result["errors"] += 1
            result["messages"].append(str(exc))

    if not payload_entries:
        save_contacts(contacts, contacts_path)
        return result

    result["triggered"] = True
    endpoint = str(merged_context["endpoint"])
    prefix = datetime.now().strftime("RO-%Y%m%d-%H%M%S")

    for batch_number, batch in enumerate(chunk_items(payload_entries, effective_batch_size), start=1):
        result["batches"] += 1
        selected_indexes = [i for i, _ in batch]
        registros = [payload for _, payload in batch]
        batch_id = f"{prefix}-{batch_number:03d}"

        try:
            http_status, response_json, response_text = send_batch(registros, endpoint)
            if 200 <= http_status < 300:
                mark_contacts_success(contacts, selected_indexes, batch_id, utc_now_iso())
                result["successes"] += len(selected_indexes)
                result["messages"].append(
                    f"RO batch {batch_id} sent successfully ({len(selected_indexes)} registros)."
                )
            else:
                error_message = response_text or json.dumps(response_json, ensure_ascii=False)
                mark_contacts_error(contacts, selected_indexes, error_message)
                result["errors"] += len(selected_indexes)
                result["messages"].append(
                    f"RO batch {batch_id} failed with HTTP {http_status}: {error_message[:300]}"
                )
        except requests.RequestException as exc:
            mark_contacts_error(contacts, selected_indexes, str(exc))
            result["errors"] += len(selected_indexes)
            result["messages"].append(f"RO batch {batch_id} network error: {exc}")

    save_contacts(contacts, contacts_path)
    result["skipped"] = len(contacts) - result["eligible"]
    return result
