"""
RO / Calltech registration helper.

This file is intentionally self-contained and heavily commented so it is easy
to tweak later, especially around payload assembly.
"""
from __future__ import annotations

from colorama import Fore, Style
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

import settings


CONTACTS_FILE = Path("contacts.json")


def load_contacts(path: str | Path = CONTACTS_FILE) -> list[dict[str, Any]]:
    """Read the shared contacts file used by the WhatsApp sender."""
    target = Path(path)
    if not target.exists():
        return []
    with target.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def save_contacts(contacts: list[dict[str, Any]], path: str | Path = CONTACTS_FILE) -> None:
    """Persist contacts atomically so we do not corrupt the shared file."""
    target = Path(path)
    temp_path = target.with_suffix(target.suffix + ".tmp")
    temp_path.write_text(json.dumps(contacts, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_path.replace(target)


def utc_now_iso() -> str:
    """Return an ISO timestamp used for local audit fields."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def normalize_phone(phone: Any) -> str:
    """Keep only digits and restore the leading plus sign used in contacts.json."""
    digits = "".join(ch for ch in str(phone or "") if ch.isdigit())
    return f"+{digits}" if digits else ""


def to_calltech_timestamp(value: Any) -> str:
    """
    Convert stored timestamps to Calltech's expected format.

    The similar project sends naive local timestamps like `YYYY-MM-DDTHH:MM:SS`.
    """
    if not value:
        return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

    parsed = None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        parsed = None

    if parsed is None:
        return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

    if parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)

    return parsed.strftime("%Y-%m-%dT%H:%M:%S")


def is_successful_send(contact: dict[str, Any]) -> bool:
    """A contact is eligible only after a successful send persisted in contacts.json."""
    sent_at = str(contact.get("sentAt") or "")
    return bool(contact.get("sent")) and bool(sent_at) and not sent_at.startswith("ERROR")


def is_ro_pending(contact: dict[str, Any]) -> bool:
    """Prevent duplicate RO insertions for items already registered."""
    return is_successful_send(contact) and not bool(contact.get("roRegistered"))


def build_origem(context: dict[str, Any]) -> str:
    """Keep the source marker in one place for easier future changes."""
    return str(context.get("origem") or settings.RO_ORIGEM)[:50]


def extract_codigo_campanha(raw_value: Any) -> str:
    """
    Extract only the digits from the campaign label.

    Example:
    - '000033 - Prime - Extrajudicial' -> '000033'
    """
    digits = "".join(ch for ch in str(raw_value or "") if ch.isdigit())
    if not digits:
        fallback = "".join(ch for ch in str(settings.RO_CODIGO_CAMPANHA or "") if ch.isdigit())
        return fallback or "000000"
    return digits


def derive_campanha_id(codigo_campanha: str) -> int:
    """
    campanhaId is derived from codigoCampanha:
    - remove leading zeros
    - add 2
    """
    codigo_base = int(codigo_campanha or "0")
    return codigo_base + 2


def build_historico(context: dict[str, Any], contact: dict[str, Any]) -> str:
    """
    Build the audit string sent to Calltech.

    If you need to change the textual format, this is the safest place to edit.
    """
    parceiro = str(context.get("parceiro") or settings.RO_PARCEIRO)
    telefone = normalize_phone(contact.get("phone")).replace("+55", "", 1).replace("+", "")
    message = str(contact.get("message") or "")
    button_url = str(contact.get("buttonUrl") or "").strip()

    historico = f"{parceiro} ({telefone}): {message}"
    if button_url:
        historico = f"{historico} {button_url}"
    return historico[:800]


def build_payload_item(context: dict[str, Any], contact: dict[str, Any]) -> dict[str, Any]:
    """
    Assemble one Calltech registro.

    This is the main payload area to adjust later.
    If Calltech changes field names or you need extra logic, edit here first.
    """
    pessoa_id = contact.get("pessoaId")
    if pessoa_id in (None, ""):
        raise ValueError(f"Contato {contact.get('phone')} sem pessoaId para registrar no RO.")

    codigo_campanha = extract_codigo_campanha(context.get("codigoCampanha"))
    campanha_id = derive_campanha_id(codigo_campanha)
    data_inicio = to_calltech_timestamp(contact.get("sentAt"))
    data_fim = to_calltech_timestamp(contact.get("deliveredAt") or contact.get("sentAt"))

    return {
        "resumoId": int(context.get("resumoId", settings.RO_RESUMO_ID)),
        "operadorId": int(context.get("operadorId", settings.RO_OPERADOR_ID)),
        "codigoCampanha": codigo_campanha,
        "campanhaId": campanha_id,
        "dataHora": data_inicio,
        "dataInicio": data_inicio,
        "dataFim": data_fim,
        "pessoaId": int(pessoa_id),
        "origem": build_origem(context),
        "historico": build_historico(context, contact),
    }


def chunk_items(items: list[Any], chunk_size: int) -> list[list[Any]]:
    """Split the registros list into Calltech-safe batches."""
    return [items[index : index + chunk_size] for index in range(0, len(items), chunk_size)]


def mark_contacts_success(
    contacts: list[dict[str, Any]],
    selected_indexes: list[int],
    batch_id: str,
    registered_at: str,
) -> None:
    """Mark contacts that were accepted for RO so they do not repeat."""
    for index in selected_indexes:
        contacts[index]["roRegistered"] = True
        contacts[index]["roRegisteredAt"] = registered_at
        contacts[index]["roBatchId"] = batch_id
        contacts[index]["roStatus"] = "success"
        contacts[index]["roError"] = None


def mark_contacts_error(contacts: list[dict[str, Any]], selected_indexes: list[int], error_message: str) -> None:
    """Store the error but keep the contact pending for a future retry."""
    for index in selected_indexes:
        contacts[index]["roRegistered"] = False
        contacts[index]["roStatus"] = "error"
        contacts[index]["roError"] = error_message[:500]


def send_batch(batch: list[dict[str, Any]], endpoint: str) -> tuple[int, dict[str, Any], str]:
    """Send one chunk to Calltech using the same wrapper format as the similar project."""
    print(batch)
    response = requests.post(endpoint, json={"registros": batch}, timeout=settings.RO_TIMEOUT_SECONDS,)
    #return 200, {}, ""
    response_text = response.text
    print(response.status_code, response_text)
    try:
        response_json = response.json()
    except ValueError:
        response_json = {}
    print(response_json)
    return response.status_code, response_json, response_text


def build_ro_context(overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    """Centralize defaults so frontend integration stays small."""
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
        context.update({key: value for key, value in overrides.items() if value not in (None, "")})
    return context


def process_ro_after_run(
    context: dict[str, Any] | None = None,
    contacts_path: str | Path = CONTACTS_FILE,
    trigger_min: int | None = None,
    batch_size: int | None = None,
    run_completed: bool = False,
) -> dict[str, Any]:
    """
    Register eligible WhatsApp sends in Calltech after the disparo finishes.

    Trigger rule:
    - fire immediately when at least `trigger_min` contacts are eligible
    - also fire when the disparo has completed, even if the eligible count is below the minimum
    - never resend contacts already marked with `roRegistered=true`
    """

    merged_context = build_ro_context(context)
    contacts = load_contacts(contacts_path)
    eligible_entries: list[tuple[int, dict[str, Any]]] = [
        (index, contact) for index, contact in enumerate(contacts) if is_ro_pending(contact)
    ]

    effective_trigger = trigger_min if trigger_min is not None else settings.RO_TRIGGER_MIN_COUNT
    effective_batch_size = batch_size if batch_size is not None else settings.RO_BATCH_SIZE

    result = {
        "triggered": False,
        "eligible": len(eligible_entries),
        "batches": 0,
        "successes": 0,
        "errors": 0,
        "skipped": 0,
        "messages": [],
    }

    if len(eligible_entries) < effective_trigger and not run_completed:
        result["messages"].append(
            f"RO skipped: {len(eligible_entries)} eligible contacts, minimum is {effective_trigger}."
        )
        return result

    if len(eligible_entries) < effective_trigger and run_completed:
        result["messages"].append(
            f"RO processing end-of-run remainder: {len(eligible_entries)} eligible contacts below the minimum {effective_trigger}."
        )

    #print(Fore.MAGENTA + f'{contacts}', Style.RESET_ALL)
    #print(Fore.LIGHTRED_EX + f'{context}', Style.RESET_ALL)
    #print(Fore.RED + f'{eligible_entries}', Style.RESET_ALL)

    payload_entries: list[tuple[int, dict[str, Any]]] = []
    for index, contact in eligible_entries:
        try:
            payload_entries.append((index, build_payload_item(merged_context, contact)))
        except Exception as exc:
            contacts[index]["roRegistered"] = False
            contacts[index]["roStatus"] = "error"
            contacts[index]["roError"] = str(exc)[:500]
            result["errors"] += 1
            result["messages"].append(str(exc))

    if not payload_entries:
        save_contacts(contacts, contacts_path)
        return result

    result["triggered"] = True
    endpoint = str(merged_context["endpoint"])
    batch_identifier_prefix = datetime.now().strftime("RO-%Y%m%d-%H%M%S")
    print(Fore.LIGHTCYAN_EX + f'{payload_entries}', Style.RESET_ALL)

    for batch_number, batch in enumerate(chunk_items(payload_entries, effective_batch_size), start=1):
        result["batches"] += 1
        selected_indexes = [index for index, _payload in batch]
        registros = [payload for _index, payload in batch]
        batch_id = f"{batch_identifier_prefix}-{batch_number:03d}"
        registered_at = utc_now_iso()

        try:
            http_status, response_json, response_text = send_batch(registros, endpoint)
            if 200 <= http_status < 300:
                mark_contacts_success(contacts, selected_indexes, batch_id, registered_at)
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
