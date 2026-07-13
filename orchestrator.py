"""
AutoWpp 2 — Orchestrator

Coordinates the three phases of a dispatch run:

  1. AUTH     — spawn one Node bot per account in `auth` mode, wait for QR scans.
  2. PREPARE  — build contacts.json (file / database / test) and distribute
                contacts round-robin across authenticated accounts.
  3. SEND     — spawn the bots in `send` mode; each bot appends its progress
                to runtime/updates_<account>.jsonl; this module merges those
                updates into contacts.json continuously (no write races).

After the run, a snapshot is stored in logs/ and ro_service (optional)
registers successful sends in Calltech.

CLI:
    python orchestrator.py --chips 2
    python orchestrator.py --chips 3 --message "Olá NOME_DO_CLIENTE..."
    python orchestrator.py --chips 2 --csv contatos.xlsx
    python orchestrator.py --test --chips 1
    python orchestrator.py --chips 2 --csv contatos.csv --skip-ro
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

import settings
import contacts_loader
from contacts_loader import CONTACTS_FILE

ROOT = Path(__file__).resolve().parent
RUNTIME_DIR = ROOT / "runtime"
LOGS_DIR = ROOT / "logs"
BOT_SCRIPT = ROOT / "bot" / "index.js"
AUTH_DIR = ROOT / ".wwebjs_auth"

RUNTIME_DIR.mkdir(exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)

LogFn = Callable[[str], None]


def _default_log(message: str) -> None:
    print(f"[orchestrator] {message}", flush=True)


# ---------------------------------------------------------------------------
# Process registry + stop flags (used by the frontend Stop buttons)
# ---------------------------------------------------------------------------
_PROCESS_LOCK = threading.Lock()
_PROCESSES: dict[str, subprocess.Popen] = {}   # "mode:account" -> Popen
_STOP = {"auth": False, "send": False}


def stop_requested(mode: str) -> bool:
    return _STOP.get(mode, False)


def _reset_stop(mode: str) -> None:
    _STOP[mode] = False


def _register(mode: str, account: str, process: subprocess.Popen) -> None:
    with _PROCESS_LOCK:
        _PROCESSES[f"{mode}:{account}"] = process


def _kill_tree(process: subprocess.Popen) -> None:
    """Kill a bot process together with its Chrome children (Windows/POSIX)."""
    if process.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                capture_output=True,
            )
        else:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
    except Exception:
        try:
            process.kill()
        except Exception:
            pass


def request_stop(mode: str, log: LogFn = _default_log) -> int:
    """
    Stop button entry point: raise the stop flag for a phase and kill every
    bot process of that mode. Returns how many processes were terminated.
    """
    _STOP[mode] = True
    killed = 0
    with _PROCESS_LOCK:
        for key, process in list(_PROCESSES.items()):
            if key.startswith(f"{mode}:") and process.poll() is None:
                _kill_tree(process)
                killed += 1
            if process.poll() is not None:
                _PROCESSES.pop(key, None)
    log(f"Stop requested for '{mode}': {killed} bot process(es) terminated.")
    return killed



def account_ids(chips: int) -> list[str]:
    chips = max(1, min(settings.MAX_ACCOUNTS, int(chips)))
    return [f"account_{i}" for i in range(1, chips + 1)]


# ---------------------------------------------------------------------------
# Runtime file helpers
# ---------------------------------------------------------------------------

def status_path(account: str) -> Path:
    return RUNTIME_DIR / f"status_{account}.json"


def qr_path(account: str) -> Path:
    return RUNTIME_DIR / f"qr_{account}.txt"


def updates_path(account: str) -> Path:
    return RUNTIME_DIR / f"updates_{account}.jsonl"


def read_status(account: str) -> dict[str, Any]:
    target = status_path(account)
    if not target.exists():
        return {"account": account, "state": "offline"}
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {"account": account, "state": "unknown"}


def clear_runtime(accounts: list[str], clear_updates: bool = True) -> None:
    for account in accounts:
        for target in ([qr_path(account), status_path(account)] +
                       ([updates_path(account)] if clear_updates else [])):
            target.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Bot process management
# ---------------------------------------------------------------------------

def spawn_bot(account: str, mode: str, log: LogFn = _default_log) -> subprocess.Popen:
    cmd = ["node", str(BOT_SCRIPT), account, mode, str(CONTACTS_FILE)]
    log(f"Starting bot: {' '.join(cmd)}")
    kwargs: dict[str, Any] = {}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    process = subprocess.Popen(
        cmd,
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        **kwargs,
    )
    _register(mode, account, process)
    return process


def _pump_output(process: subprocess.Popen, log: LogFn) -> None:
    """Forward child stdout lines to the log function (non-blocking-ish)."""
    if process.stdout is None:
        return
    import threading

    def reader() -> None:
        for line in process.stdout:  # type: ignore[union-attr]
            line = line.rstrip()
            if line:
                log(line)

    threading.Thread(target=reader, daemon=True).start()


# ---------------------------------------------------------------------------
# Phase 1 — authentication
# ---------------------------------------------------------------------------

def authenticate(accounts: list[str], sequential: bool = False, log: LogFn = _default_log,
                 timeout: Optional[int] = None, max_retries: Optional[int] = None) -> list[str]:
    """
    Run each bot in auth mode until it becomes `ready` (session persisted).

    If an account fails (error state, non-zero exit or timeout), it is
    retried automatically up to `max_retries` extra times — each retry spawns
    a fresh bot process, which produces a NEW QR code to scan.

    Returns the list of accounts that authenticated successfully.
    """
    timeout = timeout or settings.AUTH_TIMEOUT_SECONDS
    retries = settings.AUTH_MAX_RETRIES if max_retries is None else max_retries
    _reset_stop("auth")
    clear_runtime(accounts, clear_updates=False)
    authenticated: list[str] = []

    def wait_for(account: str, process: subprocess.Popen) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if stop_requested("auth"):
                _kill_tree(process)
                return False
            if process.poll() is not None:
                # auth mode exits 0 right after `ready`
                return process.returncode == 0 and read_status(account).get("state") in ("ready", "authenticated")
            state = read_status(account).get("state")
            if state == "error":
                return False
            time.sleep(1)
        log(f"{account}: authentication timeout after {timeout}s")
        process.kill()
        return False

    def attempt_until_ok(account: str) -> bool:
        for attempt in range(1, retries + 2):  # first try + retries
            if stop_requested("auth"):
                return False
            if attempt > 1:
                log(f"{account}: retrying authentication (attempt {attempt}/{retries + 1}) — new QR incoming...")
                qr_path(account).unlink(missing_ok=True)
                status_path(account).unlink(missing_ok=True)
                time.sleep(2)
            process = spawn_bot(account, "auth", log)
            _pump_output(process, log)
            if wait_for(account, process):
                return True
            if process.poll() is None:
                process.kill()
            log(f"{account}: authentication attempt {attempt} failed.")
        return False

    if sequential:
        for account in accounts:
            log(f"=== Authenticating {account} (scan the QR code) ===")
            if attempt_until_ok(account):
                authenticated.append(account)
                log(f"{account}: authenticated ✔")
            else:
                log(f"{account}: authentication FAILED after {retries + 1} attempts ✖")
    else:
        # First round in parallel, then retry the failures (also in parallel).
        pending = list(accounts)
        for round_number in range(1, retries + 2):
            if not pending or stop_requested("auth"):
                break
            if round_number > 1:
                log(f"Retry round {round_number - 1}: re-authenticating {pending} — new QR codes incoming...")
                for account in pending:
                    qr_path(account).unlink(missing_ok=True)
                    status_path(account).unlink(missing_ok=True)
                time.sleep(2)
            processes = {}
            for account in pending:
                processes[account] = spawn_bot(account, "auth", log)
                _pump_output(processes[account], log)
            failed: list[str] = []
            for account, process in processes.items():
                if wait_for(account, process):
                    authenticated.append(account)
                    log(f"{account}: authenticated ✔")
                else:
                    if process.poll() is None:
                        process.kill()
                    failed.append(account)
                    log(f"{account}: authentication attempt {round_number} failed.")
            pending = failed
        if stop_requested("auth"):
            log("Authentication stopped by user.")
            for account in pending:
                qr_path(account).unlink(missing_ok=True)
        else:
            for account in pending:
                log(f"{account}: authentication FAILED after {retries + 1} attempts ✖")

    return authenticated


# ---------------------------------------------------------------------------
# Unauthenticate (logout) accounts
# ---------------------------------------------------------------------------

def unauthenticate(accounts: list[str], log: LogFn = _default_log, timeout: int = 60) -> list[str]:
    """
    Remove authentication from the given accounts:

    1. Spawn each bot in `logout` mode — it performs client.logout(), which
       unlinks the device on WhatsApp's side (disappears from
       "Aparelhos conectados").
    2. Whatever happens, wipe the local session folder
       (.wwebjs_auth/session-<account>) and the runtime files, so the next
       auth starts from a clean QR.

    Returns the list of accounts whose sessions were removed.
    """
    removed: list[str] = []
    for account in accounts:
        session_dir = AUTH_DIR / f"session-{account}"
        has_session = session_dir.exists()

        if has_session:
            log(f"{account}: logging out (unlinking device)...")
            process = spawn_bot(account, "logout", log)
            _pump_output(process, log)
            deadline = time.time() + timeout
            while time.time() < deadline and process.poll() is None:
                time.sleep(1)
            if process.poll() is None:
                log(f"{account}: logout timed out — forcing local session removal.")
                _kill_tree(process)
        else:
            log(f"{account}: no local session found.")

        shutil.rmtree(session_dir, ignore_errors=True)
        qr_path(account).unlink(missing_ok=True)
        status_path(account).unlink(missing_ok=True)
        removed.append(account)
        log(f"{account}: session removed ✔")

    # LocalAuth cache is shared; leave .wwebjs_cache in place (harmless).
    return removed


# ---------------------------------------------------------------------------
# Phase 2 — queue preparation
# ---------------------------------------------------------------------------

def prepare_queue(
    accounts: list[str],
    csv_path: Optional[str] = None,
    message: Optional[str] = None,
    button_url: Optional[str] = None,
    test: bool = False,
    log: LogFn = _default_log,
) -> dict[str, Any]:
    if test:
        df = contacts_loader.test_dataframe()
        log("Using TEST dataframe.")
    elif csv_path:
        df = contacts_loader.load_file(csv_path)
        log(f"Loaded {len(df)} rows from {csv_path}.")
    else:
        log("No file provided — loading contacts from SQL Server...")
        df = contacts_loader.load_database()
        log(f"Loaded {len(df)} rows from database.")

    summary = contacts_loader.build_queue(df, accounts, message=message, button_url=button_url)
    contacts_loader.write_queue(summary["contacts"])

    per_account = {a: sum(1 for c in summary["contacts"] if c["sentBy"] == a) for a in accounts}
    log(
        f"Queue ready: {len(summary['contacts'])} contacts "
        f"(invalid: {summary['invalid']}, duplicated: {summary['duplicated']}, "
        f"already sent today: {summary['deduped']}) → {per_account}"
    )
    return summary


# ---------------------------------------------------------------------------
# Phase 3 — send + merge
# ---------------------------------------------------------------------------

def merge_updates(accounts: list[str]) -> dict[str, int]:
    """
    Apply every JSONL update produced by the bots to contacts.json.
    Idempotent: reads all lines each time and re-applies them.
    """
    try:
        contacts = json.loads(CONTACTS_FILE.read_text(encoding="utf-8"))
    except (ValueError, OSError, FileNotFoundError):
        return {"sent": 0, "failed": 0, "delivered": 0, "total": 0}

    by_phone = {c.get("phone"): c for c in contacts}

    for account in accounts:
        target = updates_path(account)
        if not target.exists():
            continue
        for line in target.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                update = json.loads(line)
            except ValueError:
                continue
            contact = by_phone.get(update.get("phone"))
            if not contact:
                continue
            for field in ("sent", "sentAt", "delivered", "deliveredAt", "ackLevel", "error"):
                if field in update:
                    contact[field] = update[field]

    tmp = CONTACTS_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(contacts, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(CONTACTS_FILE)

    return {
        "total": len(contacts),
        "sent": sum(1 for c in contacts if c.get("sent")),
        "failed": sum(1 for c in contacts if str(c.get("sentAt") or "").startswith("ERROR")),
        "delivered": sum(1 for c in contacts if c.get("delivered")),
    }


def run_send(accounts: list[str], log: LogFn = _default_log,
             progress_cb: Optional[Callable[[dict[str, int]], None]] = None) -> dict[str, int]:
    """Spawn all send bots, merge updates every few seconds until they exit."""
    _reset_stop("send")
    # fresh update logs for this run
    for account in accounts:
        updates_path(account).unlink(missing_ok=True)

    processes = {}
    for account in accounts:
        processes[account] = spawn_bot(account, "send", log)
        _pump_output(processes[account], log)
        time.sleep(3)  # stagger Chrome startups

    while any(p.poll() is None for p in processes.values()):
        if stop_requested("send"):
            log("Send phase stopped by user — terminating remaining bots...")
            for process in processes.values():
                _kill_tree(process)
            break
        stats = merge_updates(accounts)
        if progress_cb:
            progress_cb(stats)
        time.sleep(5)

    stats = merge_updates(accounts)
    if progress_cb:
        progress_cb(stats)

    for account, process in processes.items():
        log(f"{account}: exited with code {process.returncode}")
    log(f"Send phase finished: {stats}")
    return stats


# ---------------------------------------------------------------------------
# Finalization
# ---------------------------------------------------------------------------

def save_run_log(extra: dict[str, Any] | None = None) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    target = LOGS_DIR / f"run_{timestamp}.json"
    try:
        contacts = json.loads(CONTACTS_FILE.read_text(encoding="utf-8"))
    except (ValueError, OSError, FileNotFoundError):
        contacts = []
    payload = {"finishedAt": datetime.now().isoformat(), "extra": extra or {}, "contacts": contacts}
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def run_ro(context: dict[str, Any] | None = None, log: LogFn = _default_log) -> dict[str, Any]:
    if not settings.RO_ENABLED:
        log("RO disabled (RO_ENABLED=False).")
        return {"triggered": False, "messages": ["RO disabled"]}
    import ro_service

    result = ro_service.process_ro_after_run(context=context, run_completed=True)
    for message in result.get("messages", []):
        log(f"RO: {message}")
    log(f"RO summary: eligible={result['eligible']} ok={result['successes']} errors={result['errors']}")
    return result


# ---------------------------------------------------------------------------
# Full pipeline
# ---------------------------------------------------------------------------

def run_pipeline(
    chips: int,
    csv_path: Optional[str] = None,
    message: Optional[str] = None,
    button_url: Optional[str] = None,
    test: bool = False,
    skip_ro: bool = False,
    ro_context: dict[str, Any] | None = None,
    sequential_auth: bool = True,
    log: LogFn = _default_log,
    progress_cb: Optional[Callable[[dict[str, int]], None]] = None,
    preauthenticated: Optional[list[str]] = None,
) -> dict[str, Any]:
    accounts = account_ids(chips)

    if preauthenticated:
        authenticated = preauthenticated
    else:
        log(f"Phase 1/3 — authenticating {len(accounts)} account(s)...")
        authenticated = authenticate(accounts, sequential=sequential_auth, log=log)

    if not authenticated:
        raise RuntimeError("No account authenticated. Aborting.")
    log(f"Authenticated accounts: {authenticated}")

    log("Phase 2/3 — preparing queue...")
    summary = prepare_queue(
        authenticated, csv_path=csv_path, message=message, button_url=button_url, test=test, log=log
    )
    if not summary["contacts"]:
        log("Queue is empty — nothing to send.")
        return {"stats": {"total": 0, "sent": 0, "failed": 0, "delivered": 0}, "ro": None}

    log("Phase 3/3 — sending...")
    stats = run_send(authenticated, log=log, progress_cb=progress_cb)
    stopped = stop_requested("send")

    ro_result = None
    if stopped:
        log("Run was stopped — skipping RO. Use 'Processar RO agora' to register the sends that completed.")
    elif not skip_ro:
        ro_result = run_ro(context=ro_context, log=log)

    log_file = save_run_log({"stats": stats, "ro": ro_result, "stoppedByUser": stopped})
    log(f"Run log saved to {log_file}")
    return {"stats": stats, "ro": ro_result, "logFile": str(log_file)}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="AutoWpp 2 — WhatsApp dispatch orchestrator")
    parser.add_argument("--chips", type=int, default=1, help=f"Number of accounts (1-{settings.MAX_ACCOUNTS})")
    parser.add_argument("--csv", type=str, default=None, help="CSV or XLSX contact file")
    parser.add_argument("--message", type=str, default=None, help="Override the base message")
    parser.add_argument("--button-url", type=str, default=None, help="Override CONTACT_BUTTON_URL")
    parser.add_argument("--test", action="store_true", help="Use the built-in test dataframe")
    parser.add_argument("--skip-ro", action="store_true", help="Skip RO/Calltech post-processing")
    parser.add_argument("--parallel-auth", action="store_true",
                        help="Authenticate all accounts at once (QRs interleave in the terminal)")
    args = parser.parse_args()

    try:
        run_pipeline(
            chips=args.chips,
            csv_path=args.csv,
            message=args.message,
            button_url=args.button_url,
            test=args.test,
            skip_ro=args.skip_ro,
            sequential_auth=not args.parallel_auth,
        )
    except KeyboardInterrupt:
        print("\nInterrupted by user.")
        sys.exit(130)
    except Exception as exc:
        print(f"FATAL: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
