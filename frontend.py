"""
AutoWpp 2 — Gradio web frontend

Flow:
  1. Pick the number of chips and click "1) Autenticar" — a QR code appears
     for each account; scan them in WhatsApp > Aparelhos conectados.
     Accounts that fail are retried automatically (new QR); a manual
     "Reautenticar contas com falha" button covers anything left over.
  2. Write the base message and upload the CSV/XLSX. The Credor/Campanha
     columns on each contact are used for RO registration. Then click
     "2) Disparar".
  3. Follow the live progress table; RO runs automatically at the end.

Run:  python frontend.py   →  http://127.0.0.1:8502
"""
from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path

import gradio as gr
import pandas as pd
import qrcode

import settings
import orchestrator
import ro_service

RUNTIME_DIR = orchestrator.RUNTIME_DIR
MAX_ACCOUNTS = settings.MAX_ACCOUNTS

# ---------------------------------------------------------------------------
# Shared state (frontend <-> background threads)
# ---------------------------------------------------------------------------

class AppState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.phase = "idle"            # idle | auth | ready | sending | done | error
        self.accounts: list[str] = []
        self.authenticated: list[str] = []
        self.failed_auth: list[str] = []
        self.logs: list[str] = []
        self.stats = {"total": 0, "sent": 0, "failed": 0, "delivered": 0}
        self.busy = False

    def log(self, message: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        with self.lock:
            self.logs.append(f"[{stamp}] {message}")
            self.logs = self.logs[-400:]

    def text_logs(self) -> str:
        with self.lock:
            return "\n".join(self.logs[-200:])


STATE = AppState()


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------

def render_qr_images() -> list[tuple]:
    """Turn runtime/qr_<acc>.txt files into PIL images for the gallery."""
    images = []
    for account in STATE.accounts:
        qr_file = orchestrator.qr_path(account)
        if qr_file.exists():
            data = qr_file.read_text(encoding="utf-8").strip()
            if data:
                img = qrcode.make(data).get_image().convert("RGB")
                images.append((img, f"{account} — escaneie no WhatsApp"))
    return images


def status_table() -> pd.DataFrame:
    rows = []
    for account in STATE.accounts:
        status = orchestrator.read_status(account)
        rows.append(
            {
                "Conta": account,
                "Estado": status.get("state", "offline"),
                "Atribuídos": status.get("total", ""),
                "Enviados": status.get("sent", ""),
                "Falhas": status.get("failed", ""),
                "Atualizado": str(status.get("updatedAt", ""))[11:19],
            }
        )
    if not rows:
        rows = [{"Conta": "-", "Estado": "aguardando", "Atribuídos": "", "Enviados": "", "Falhas": "", "Atualizado": ""}]
    return pd.DataFrame(rows)


def progress_text() -> str:
    s = STATE.stats
    line = (
        f"Fase: {STATE.phase} | Total: {s['total']} | Enviados: {s['sent']} | "
        f"Entregues: {s['delivered']} | Falhas: {s['failed']}"
    )
    if STATE.authenticated:
        line += f" | Autenticadas: {', '.join(STATE.authenticated)}"
    if STATE.failed_auth:
        line += f" | Falha auth: {', '.join(STATE.failed_auth)}"
    return line


# ---------------------------------------------------------------------------
# Actions (background threads)
# ---------------------------------------------------------------------------

def _auth_worker(accounts: list[str], merge: bool = False) -> None:
    """Authenticate `accounts` (auto-retry inside orchestrator.authenticate)."""
    try:
        authenticated = orchestrator.authenticate(accounts, sequential=False, log=STATE.log)
        if merge:
            STATE.authenticated = sorted(set(STATE.authenticated) | set(authenticated))
        else:
            STATE.authenticated = authenticated
        STATE.failed_auth = [a for a in STATE.accounts if a not in STATE.authenticated]
        STATE.phase = "ready" if STATE.authenticated else "error"
        STATE.log(f"Autenticação concluída: {STATE.authenticated or 'nenhuma conta'}")
        if STATE.failed_auth:
            STATE.log(
                f"Contas com falha: {STATE.failed_auth} — use 'Reautenticar contas com falha' "
                f"para gerar novos QR Codes."
            )
    except Exception as exc:
        STATE.phase = "error"
        STATE.log(f"Erro na autenticação: {exc}")
    finally:
        STATE.busy = False


def start_auth(chips: float):
    if STATE.busy:
        return "Ocupado — aguarde a operação atual."
    STATE.busy = True
    STATE.phase = "auth"
    STATE.accounts = orchestrator.account_ids(int(chips))
    STATE.authenticated = []
    STATE.failed_auth = []
    STATE.log(
        f"Iniciando autenticação de {len(STATE.accounts)} conta(s) "
        f"(até {settings.AUTH_MAX_RETRIES + 1} tentativas por conta)..."
    )
    threading.Thread(target=_auth_worker, args=(STATE.accounts,), daemon=True).start()
    return "Autenticação iniciada — escaneie os QR Codes abaixo."


def retry_failed_auth():
    if STATE.busy:
        return "Ocupado — aguarde a operação atual."
    failed = [a for a in STATE.accounts if a not in STATE.authenticated]
    if not failed:
        return "Nenhuma conta com falha de autenticação."
    STATE.busy = True
    STATE.phase = "auth"
    STATE.log(f"Reautenticando contas com falha: {failed} — novos QR Codes a caminho...")
    threading.Thread(target=_auth_worker, args=(failed, True), daemon=True).start()
    return f"Reautenticando {', '.join(failed)} — escaneie os novos QR Codes."


def stop_auth():
    if STATE.phase != "auth":
        return "Nenhuma autenticação em andamento."
    killed = orchestrator.request_stop("auth", log=STATE.log)
    STATE.log("Autenticação interrompida pelo usuário.")
    return f"Autenticação interrompida ({killed} bot(s) finalizado(s))."


def stop_send():
    if STATE.phase != "sending":
        return "Nenhum disparo em andamento."
    killed = orchestrator.request_stop("send", log=STATE.log)
    STATE.log("Disparo interrompido pelo usuário. Contatos já enviados permanecem marcados.")
    return f"Disparo interrompido ({killed} bot(s) finalizado(s)). Use 'Processar RO agora' se quiser registrar o que já foi enviado."


def unauth_all():
    if STATE.busy:
        return "Ocupado — pare a operação atual antes de desautenticar."
    targets = sorted(set(STATE.accounts) | set(STATE.authenticated))
    if not targets:
        # Nothing tracked in this session: clean every session folder on disk.
        targets = sorted(
            d.name.replace("session-", "")
            for d in orchestrator.AUTH_DIR.glob("session-*")
        ) if orchestrator.AUTH_DIR.exists() else []
    if not targets:
        return "Nenhuma sessão autenticada encontrada."

    STATE.busy = True
    STATE.phase = "unauth"
    STATE.log(f"Desautenticando chips: {targets}...")

    def worker() -> None:
        try:
            removed = orchestrator.unauthenticate(targets, log=STATE.log)
            STATE.authenticated = []
            STATE.failed_auth = []
            STATE.phase = "idle"
            STATE.log(f"Desautenticação concluída: {removed}. Autentique novamente para disparar.")
        except Exception as exc:
            STATE.phase = "error"
            STATE.log(f"Erro na desautenticação: {exc}")
        finally:
            STATE.busy = False

    threading.Thread(target=worker, daemon=True).start()
    return f"Desautenticando {len(targets)} chip(s) — acompanhe nos logs."


def start_dispatch(message: str, button_url: str, upload, skip_ro: bool):
    if STATE.busy:
        return "Ocupado — aguarde a operação atual."
    if not STATE.authenticated:
        return "Nenhuma conta autenticada. Rode a etapa 1 primeiro."

    csv_path = None
    if upload is not None:
        csv_path = upload if isinstance(upload, str) else getattr(upload, "name", None)

    STATE.busy = True
    STATE.phase = "sending"
    STATE.stats = {"total": 0, "sent": 0, "failed": 0, "delivered": 0}
    STATE.log("Iniciando disparo...")

    def progress_cb(stats: dict) -> None:
        STATE.stats = stats

    def worker() -> None:
        try:
            result = orchestrator.run_pipeline(
                chips=len(STATE.authenticated),
                csv_path=csv_path,
                message=message or None,
                button_url=button_url if button_url is not None else None,
                skip_ro=skip_ro,
                log=STATE.log,
                progress_cb=progress_cb,
                preauthenticated=STATE.authenticated,
            )
            STATE.stats = result["stats"]
            STATE.phase = "done"
            STATE.log("Disparo finalizado.")
        except Exception as exc:
            STATE.phase = "error"
            STATE.log(f"Erro no disparo: {exc}")
        finally:
            STATE.busy = False

    threading.Thread(target=worker, daemon=True).start()
    return "Disparo iniciado — acompanhe o progresso abaixo."


# ---------------------------------------------------------------------------
# RO
# ---------------------------------------------------------------------------

def run_ro_now():
    try:
        result = ro_service.process_ro_after_run(run_completed=True)
        for message in result.get("messages", []):
            STATE.log(f"RO: {message}")
        return (
            f"RO: elegíveis={result['eligible']} sucesso={result['successes']} "
            f"erros={result['errors']} lotes={result['batches']}"
        )
    except Exception as exc:
        STATE.log(f"Erro no RO: {exc}")
        return f"Erro no RO: {exc}"


def sample_csv():
    df = pd.DataFrame(
        [
            ["Maria Silva", "31999999999", "12345", "maria@email.com", "Cliente prioritário", "Acme", "000033 - Prime"],
            ["João Souza", "41988888888", "67890", "joao@email.com", "Carteira B", "Acme", "000074 - Carteira B"],
        ],
        columns=["Nome", "Telefone", "pessoaId", "email", "observacao", "Credor", "Campanha"],
    )
    target = Path("samples") / "modelo_contatos.csv"
    target.parent.mkdir(exist_ok=True)
    df.to_csv(target, index=False, encoding="utf-8-sig")
    return str(target)


def refresh():
    return render_qr_images(), status_table(), progress_text(), STATE.text_logs()


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

with gr.Blocks(title="AutoWpp 2 — Disparos WhatsApp") as demo:
    gr.Markdown("## AutoWpp 2 — Orquestrador de disparos WhatsApp (multi-conta)")

    with gr.Row():
        # ------------------------------------------------------------------
        # Left column — controls (foldable sections)
        # ------------------------------------------------------------------
        with gr.Column(scale=1):
            with gr.Accordion("1) Autenticação", open=True):
                chips = gr.Slider(1, MAX_ACCOUNTS, value=1, step=1, label="Quantidade de chips")
                auth_btn = gr.Button("Autenticar chips", variant="primary")
                retry_auth_btn = gr.Button("🔁 Reautenticar contas com falha")
                with gr.Row():
                    stop_auth_btn = gr.Button("⏹️ Parar autenticação", variant="stop")
                    unauth_btn = gr.Button("🔓 Desautenticar todos os chips", variant="stop")

            with gr.Accordion("2) Mensagem e contatos", open=True):
                message = gr.Textbox(
                    label="Mensagem base (use NOME_DO_CLIENTE)",
                    value=settings.CONTACT_MESSAGE,
                    lines=6,
                )
                button_url = gr.Textbox(
                    label="URL anexada à mensagem (opcional)", value=settings.CONTACT_BUTTON_URL
                )
                upload = gr.File(
                    label="Contatos (CSV ou XLSX) — vazio = banco de dados",
                    file_types=[".csv", ".xlsx", ".xls"],
                )
                sample_btn = gr.Button("Baixar modelo CSV")
                sample_file = gr.File(label="Modelo", interactive=False)

            with gr.Accordion("3) Registro RO/Calltech", open=True):
                gr.Markdown(
                    "Os valores de **Credor** e **Campanha** são lidos das colunas "
                    "correspondentes no CSV/XLSX de cada contato."
                )
                skip_ro = gr.Checkbox(label="Pular registro RO/Calltech", value=not settings.RO_ENABLED)
                ro_btn = gr.Button("Processar RO agora")

            with gr.Row():
                send_btn = gr.Button("🚀 Disparar", variant="primary", size="lg")
                stop_send_btn = gr.Button("⏹️ Parar disparo", variant="stop", size="lg")
            action_status = gr.Textbox(label="Status da ação", interactive=False)

        # ------------------------------------------------------------------
        # Right column — monitoring (foldable sections)
        # ------------------------------------------------------------------
        with gr.Column(scale=2):
            progress = gr.Textbox(label="Progresso", interactive=False)
            with gr.Accordion("QR Codes pendentes", open=True):
                qr_gallery = gr.Gallery(show_label=False, columns=3, height=900)
            with gr.Accordion("Contas", open=True):
                table = gr.Dataframe(show_label=False, interactive=False)
            with gr.Accordion("Logs", open=False):
                logs = gr.Textbox(show_label=False, lines=16, interactive=False)

    # ----------------------------------------------------------------------
    # Wiring
    # ----------------------------------------------------------------------
    auth_btn.click(start_auth, inputs=[chips], outputs=[action_status])
    retry_auth_btn.click(retry_failed_auth, outputs=[action_status])
    stop_auth_btn.click(stop_auth, outputs=[action_status])
    stop_send_btn.click(stop_send, outputs=[action_status])
    unauth_btn.click(unauth_all, outputs=[action_status])
    send_btn.click(
        start_dispatch,
        inputs=[message, button_url, upload, skip_ro],
        outputs=[action_status],
    )
    ro_btn.click(run_ro_now, outputs=[action_status])
    sample_btn.click(sample_csv, outputs=[sample_file])

    timer = gr.Timer(2.0)
    timer.tick(refresh, outputs=[qr_gallery, table, progress, logs])


if __name__ == "__main__":
    demo.launch(server_name=settings.FRONTEND_HOST, server_port=settings.FRONTEND_PORT, show_error=True)
