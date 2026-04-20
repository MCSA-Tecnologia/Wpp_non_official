"""
Gradio Frontend for WhatsApp Multi-Account Orchestrator
Dynamically creates per-account displays based on a dropdown selector (1–4).
Shows QR codes and live output per account; disables the Run button while active.
"""

import io
import sys
import threading
import re
import time
import base64
from datetime import datetime
from pathlib import Path
import gradio as gr
import pandas as pd
import pyodbc

import orchestrator
import ro_service
import settings

MAX_ACCOUNTS = 6

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

ACCOUNT_LINE_RE = re.compile(r"^\[([^\]]+)\]\s*(.*)", re.DOTALL)


def _make_account(n: int) -> dict:
    """Build a fresh account dict for account number *n* (1-based)."""
    return {
        "id": f"account_{n}",
        "name": f"Account {n}",
        "process": None,
        "authenticated": False,
        "ready": False,
        "consecutive_uses": 0,
    }


def _set_accounts(count: int):
    """Rebuild orchestrator.ACCOUNTS in-place to have *count* entries."""
    orchestrator.ACCOUNTS.clear()
    for i in range(1, count + 1):
        orchestrator.ACCOUNTS.append(_make_account(i))


def _empty_logs(count: int):
    return {f"Account {i}": "" for i in range(1, count + 1)}


def fetch_credor_campanha_data():
    """Fetch distinct CREDOR/CAMPANHA pairs and build a mapping by creditor."""
    query = """
    SELECT DISTINCT
        [CAMPANHA],
        [CREDOR]
    FROM [Candiotto_DBA].[dbo].[tabelatitulos]
    WHERE [CREDOR] IS NOT NULL
      AND [CAMPANHA] IS NOT NULL
    ORDER BY [CREDOR], [CAMPANHA]
    """

    conn = None
    try:
        conn = pyodbc.connect(
            'DRIVER={SQL Server};SERVER=' + settings.SERVER
            + ';DATABASE=' + settings.DATABASE
            + ';UID=' + settings.USERNAME
            + ';PWD=' + settings.PASSWORD
        )
        df = pd.read_sql_query(query, conn)
    except Exception as e:
        print(f"Erro ao buscar credores/campanhas: {e}")
        df = pd.DataFrame(columns=["CREDOR", "CAMPANHA"])
    finally:
        if conn is not None:
            conn.close()

    mapping = {}
    if not df.empty:
        df["CREDOR"] = df["CREDOR"].astype(str).str.strip()
        df["CAMPANHA"] = df["CAMPANHA"].astype(str).str.strip()
        df = df[(df["CREDOR"] != "") & (df["CAMPANHA"] != "")]
        for credor, group in df.groupby("CREDOR", sort=True):
            mapping[credor] = sorted(group["CAMPANHA"].drop_duplicates().tolist())

    return mapping


def load_contacts_input_file(file_path: str) -> pd.DataFrame:
    """
    Load contact input from CSV or Excel.

    Supported examples:
    - Nome/Telefone CSV
    - pessoaId,email,telefone,observacao XLSX
    - pessoaId;email;telefone;observacao CSV
    """
    suffix = Path(file_path).suffix.lower()

    if suffix == ".xlsx":
        df = pd.read_excel(file_path, dtype=object)
    else:
        df = pd.read_csv(file_path, dtype=str, sep=None, engine="python")

    df.columns = [str(column).strip() for column in df.columns]

    rename_map = {}
    for column in df.columns:
        lowered = column.lower()
        if lowered == "telefone":
            rename_map[column] = "Telefone"
        elif lowered == "nome":
            rename_map[column] = "Nome"
        elif lowered == "pessoaid":
            rename_map[column] = "pessoaId"
        elif lowered == "email":
            rename_map[column] = "email"
        elif lowered == "observacao":
            rename_map[column] = "observacao"
        else:
            rename_map[column] = column

    return df.rename(columns=rename_map)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

class _TeeWriter(io.TextIOBase):
    """A writer that intercepts every line and routes it to the runner."""

    def __init__(self, runner: "OrchestratorRunner", original):
        self._runner = runner
        self._original = original

    def write(self, s: str):
        if s:
            for line in s.splitlines(keepends=True):
                self._runner._route_line(line)
            # Also forward to the real stdout so the terminal still works
            if self._original:
                self._original.write(s)
        return len(s) if s else 0

    def flush(self):
        if self._original:
            self._original.flush()


class OrchestratorRunner:
    def __init__(self):
        self.running = False
        self.general_log = ""
        self.account_logs: dict[str, str] = {}
        self.account_count = 1
        self.lock = threading.Lock()

    def start(self, count: int, message: str = "", csv_path: str = ""):
        self.running = True
        self.account_count = count
        self.general_log = ""
        self.account_logs = _empty_logs(count)

        # Rebuild orchestrator.ACCOUNTS in-place before calling main()
        _set_accounts(count)
        # Reset orchestrator globals that accumulate across runs
        orchestrator.authenticated_accounts.clear()
        orchestrator.contacts_json_built = False
        orchestrator.pending_contacts_df = None
        orchestrator.message_variants.clear()
        orchestrator.csv_contacts_df = None

        # Load CSV/XLSX if provided
        if csv_path:
            try:
                df = load_contacts_input_file(csv_path)
                if "Telefone" not in df.columns:
                    self._route_line("ERROR: input file must have a 'Telefone' or 'telefone' column.\n")
                else:
                    orchestrator.csv_contacts_df = df
                    self._route_line(f"Input file loaded: {len(df)} contact(s)\n")
            except Exception as e:
                self._route_line(f"ERROR loading input file: {e}\n")

        custom_msg = message.strip() if message and message.strip() else None

        # Redirect stdout so we capture all print() output from orchestrator
        original_stdout = sys.stdout
        sys.stdout = _TeeWriter(self, original_stdout)
        try:
            orchestrator.main(custom_message=custom_msg)
        except SystemExit:
            pass  # orchestrator calls sys.exit on failure; absorb it
        except Exception as e:
            self._route_line(f"ERROR: {e}\n")
        finally:
            sys.stdout = original_stdout
            self.running = False

    def _route_line(self, line: str):
        m = ACCOUNT_LINE_RE.match(line.rstrip())
        with self.lock:
            if m:
                name = m.group(1).strip()
                content = m.group(2) + "\n"
                if name in self.account_logs:
                    self.account_logs[name] += content
                else:
                    self.general_log += line
            else:
                self.general_log += line

    def snapshot(self):
        with self.lock:
            return self.general_log, dict(self.account_logs), self.account_count

    def stop(self):
        # Stop all running bot processes managed by orchestrator
        orchestrator.stop_bots(orchestrator.ACCOUNTS)
        self.running = False


runner = OrchestratorRunner()

# ---------------------------------------------------------------------------
# Gradio callbacks
# ---------------------------------------------------------------------------

def on_account_count_change(count_str):
    """When the dropdown changes, show/hide account boxes."""
    count = int(count_str)
    _set_accounts(count)
    updates = []
    for i in range(1, MAX_ACCOUNTS + 1):
        if i <= count:
            updates.append(gr.update(visible=True, value="", label=f"Chip {i}"))
        else:
            updates.append(gr.update(visible=False, value=""))
    return updates


def update_campanha_dropdown(selected_credor, credor_campanha_map):
    campanhas = credor_campanha_map.get(selected_credor, []) if credor_campanha_map else []
    value = campanhas[0] if campanhas else None
    return gr.update(choices=campanhas, value=value)


def refresh_credor_campanha_options():
    credor_campanha_map = fetch_credor_campanha_data()
    credores = sorted(credor_campanha_map.keys())
    selected_credor = credores[0] if credores else None
    campanhas = credor_campanha_map.get(selected_credor, []) if selected_credor else []
    selected_campanha = campanhas[0] if campanhas else None

    return (
        credor_campanha_map,
        gr.update(choices=credores, value=selected_credor),
        gr.update(choices=campanhas, value=selected_campanha),
    )

def _sanitize_filename_part(value: str | None) -> str:
    if not value:
        return ""
    sanitized = re.sub(r"[^A-Za-z0-9_-]+", "_", str(value).strip())
    return sanitized.strip("_")


def fetch_client_list_for_download() -> pd.DataFrame:
    query = """
    SELECT TOP (100)
        [Pessoa_ID],
        [NOME_RAZAO_SOCIAL],
        [NUMERO_CONTRATO],
        [Faixa_Aging],
        [STATUS_TITULO]
    FROM [Candiotto_DBA].[dbo].[tabelatitulos]
    """
    conn = None
    try:
        conn = pyodbc.connect(
            'DRIVER={SQL Server};SERVER=' + settings.SERVER
            + ';DATABASE=' + settings.DATABASE
            + ';UID=' + settings.USERNAME
            + ';PWD=' + settings.PASSWORD
        )
        return pd.read_sql_query(query, conn)
    finally:
        if conn is not None:
            conn.close()


def _build_client_download_xlsx(
    df: pd.DataFrame,
    selected_credor: str | None,
    selected_campanha: str | None,
) -> tuple[str, str]:
    export_df = df.rename(
        columns={
            "Pessoa_ID": "pessoaId",
            "NOME_RAZAO_SOCIAL": "email",
            "NUMERO_CONTRATO": "telefone",
            "STATUS_TITULO": "observacao",
        }
    )
    export_df = export_df[["pessoaId", "email", "telefone", "observacao"]]
    export_df = export_df.fillna("")

    filename_parts = ["client_list"]
    credor_part = _sanitize_filename_part(selected_credor)
    campanha_part = _sanitize_filename_part(selected_campanha)
    if credor_part:
        filename_parts.append(credor_part)
    if campanha_part:
        filename_parts.append(campanha_part)
    filename_parts.append(datetime.now().strftime("%Y%m%d_%H%M%S"))

    filename = f"{'_'.join(filename_parts)}.xlsx"
    output_buffer = io.BytesIO()
    with pd.ExcelWriter(output_buffer, engine="openpyxl") as writer:
        export_df.to_excel(writer, index=False)
    output_buffer.seek(0)

    return filename, base64.b64encode(output_buffer.getvalue()).decode("ascii")


def download_client_list(selected_credor, selected_campanha):
    try:
        df = fetch_client_list_for_download()
        filename, file_b64 = _build_client_download_xlsx(df, selected_credor, selected_campanha)
        status = (
            f"Arquivo gerado com {len(df)} cliente(s) no formato "
            "`pessoaId,email,telefone,observacao`."
        )
        return file_b64, filename, status
    except Exception as e:
        return "", "", f"Erro ao gerar lista de clientes: {e}"


def run_orchestrator(count_str, message_text, csv_file, selected_credor, selected_campanha):
    count = int(count_str)
    csv_path = csv_file if csv_file else ""

    if runner.running:
        yield _build_outputs("Orchestrator is already running.\n", _empty_logs(count), count, running=True)
        return

    t = threading.Thread(target=runner.start, args=(count, message_text, csv_path), daemon=True)
    t.start()

    while runner.running or t.is_alive():
        time.sleep(1)
        general, account_logs, cnt = runner.snapshot()
        yield _build_outputs(general, account_logs, cnt, running=True)

    general, account_logs, cnt = runner.snapshot()
    ro_result = ro_service.process_ro_after_run(
        context={
            "credor": selected_credor,
            "campanhaNome": selected_campanha,
            "codigoCampanha": selected_campanha or settings.RO_CODIGO_CAMPANHA,
        },
        run_completed=True,
    )
    if ro_result["triggered"]:
        general += (
            "\nRO registration finished."
            f" Eligible: {ro_result['eligible']}"
            f" | Successes: {ro_result['successes']}"
            f" | Errors: {ro_result['errors']}"
            f" | Batches: {ro_result['batches']}\n"
        )
    if ro_result["messages"]:
        general += "\n".join(ro_result["messages"]) + "\n"
    general += "\nOrchestrator finished.\n"
    yield _build_outputs(general, account_logs, cnt, running=False)


def stop_orchestrator():
    runner.stop()
    general, account_logs, cnt = runner.snapshot()
    general += "\nOrchestrator stopped by user.\n"
    return _build_outputs(general, account_logs, cnt, running=False)


def _build_outputs(general_log, account_logs, count, running=False):
    """
    Returns: [run_btn, stop_btn, dropdown, general_box, box_1, box_2, box_3, box_4]
    """
    btn_run = gr.update(interactive=not running, variant="primary" if not running else "secondary")
    btn_stop = gr.update(interactive=running)
    dropdown = gr.update(interactive=not running)
    outputs = [btn_run, btn_stop, dropdown, general_log]
    for i in range(1, MAX_ACCOUNTS + 1):
        name = f"Account {i}"
        if i <= count:
            outputs.append(gr.update(visible=True, value=account_logs.get(name, "")))
        else:
            outputs.append(gr.update(visible=False, value=""))
    return outputs


# ---------------------------------------------------------------------------
# Build the UI
# ---------------------------------------------------------------------------

UI_CSS = """
.gradio-container,
.gradio-container *:not(.qr-output textarea) {
    font-family: 'Times New Roman', Times, serif !important;
}
.qr-output textarea {
    font-family: 'Courier New', Courier, monospace !important;
    font-size: 10px !important;
    line-height: 1.0 !important;
    white-space: pre !important;
    overflow-x: auto !important;
    letter-spacing: 0px !important;
}
footer { display: none !important; }
"""

def build_ui():
    account_boxes = []
    initial_count = len(orchestrator.ACCOUNTS)
    initial_credor_campanha_map = {}

    with gr.Blocks(title="WhatsApp Orchestrator") as demo:
        gr.Markdown("# MCSA WhatsApp Multi-Account Orchestrator")

        with gr.Row():
            message_input = gr.Textbox(
                label="Mensagem",
                placeholder="Digite a mensagem base aqui... (deixe vazio para usar a mensagem padrão do settings.py)\nUse NOME_DO_CLIENTE para personalizar com o primeiro nome do CSV.",
                lines=8,
                max_lines=8,
                interactive=True,
                scale=3,
            )
            csv_upload = gr.File(
                label="Arquivo de Contatos (CSV/XLSX)",
                file_types=[".csv", ".xlsx"],
                type="filepath",
                scale=1,
            )
        with gr.Row():
            run_btn = gr.Button("▶  Run Disparos", variant="primary", scale=3)
            stop_btn = gr.Button("⏹  Stop", variant="stop", interactive=False, scale=1)
        with gr.Row():
            account_dropdown = gr.Dropdown(
                choices=["1", "2", "3", "4", "5", "6"],
                value=str(initial_count),
                label="Número de Chips",
                interactive=True,
                scale=1,
            )
            credor_campanha_state = gr.State(initial_credor_campanha_map)
            credor_dropdown = gr.Dropdown(
                choices=[],
                value=None,
                label="Credor",
                interactive=True,
                scale=2,
            )
            campanha_dropdown = gr.Dropdown(
                choices=[],
                value=None,
                label="Campanha",
                interactive=True,
                scale=2,
            )
            with gr.Row():
                refresh_credor_btn = gr.Button("Refresh Credores", variant="secondary", scale=1)
                download_cliente_btn = gr.Button("Baixar Clientes", variant="secondary", scale=1)
        download_client_payload = gr.Textbox(visible=False)
        download_client_filename = gr.Textbox(visible=False)
        download_status = gr.Markdown("")

        with gr.Accordion("Orchestrator Log", open=False):
            general_box = gr.Textbox(
                label="General Output",
                lines=32,
                max_lines=32,
                interactive=False,
                buttons=["copy"],
                elem_classes=["qr-output"],
            )

        gr.Markdown("### Account Outputs")
        with gr.Row():
            for i in range(1, MAX_ACCOUNTS + 1):
                with gr.Column(min_width=480):
                    box = gr.Textbox(
                        label=f"Chip {i}",
                        lines=50,
                        max_lines=50,
                        interactive=False,
                        buttons=["copy"],
                        elem_classes=["qr-output"],
                        visible=(i <= initial_count),
                    )
                    account_boxes.append(box)

        # --- Wiring ---
        all_outputs = [run_btn, stop_btn, account_dropdown, general_box] + account_boxes

        account_dropdown.change(
            fn=on_account_count_change,
            inputs=[account_dropdown],
            outputs=account_boxes,
        )

        credor_dropdown.change(
            fn=update_campanha_dropdown,
            inputs=[credor_dropdown, credor_campanha_state],
            outputs=[campanha_dropdown],
        )

        refresh_credor_btn.click(
            fn=refresh_credor_campanha_options,
            inputs=[],
            outputs=[credor_campanha_state, credor_dropdown, campanha_dropdown],
        )

        download_event = download_cliente_btn.click(
            fn=download_client_list,
            inputs=[credor_dropdown, campanha_dropdown],
            outputs=[download_client_payload, download_client_filename, download_status],
        )
        download_event.then(
            fn=None,
            inputs=[download_client_payload, download_client_filename],
            outputs=[],
            js="""
            (payloadB64, filename) => {
                if (!payloadB64 || !filename) {
                    return [];
                }

                const binary = atob(payloadB64);
                const bytes = new Uint8Array(binary.length);
                for (let i = 0; i < binary.length; i++) {
                    bytes[i] = binary.charCodeAt(i);
                }

                const blob = new Blob(
                    [bytes],
                    {
                        type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
                    }
                );
                const url = URL.createObjectURL(blob);
                const link = document.createElement('a');
                link.href = url;
                link.download = filename;
                document.body.appendChild(link);
                link.click();
                document.body.removeChild(link);
                setTimeout(() => URL.revokeObjectURL(url), 1000);
                return [];
            }
            """,
        )

        run_btn.click(
            fn=run_orchestrator,
            inputs=[account_dropdown, message_input, csv_upload, credor_dropdown, campanha_dropdown],
            outputs=all_outputs,
        )

        stop_btn.click(
            fn=stop_orchestrator,
            inputs=[],
            outputs=all_outputs,
        )

    return demo


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    demo = build_ui()
    demo.launch(
        share=True,
        favicon_path="src/icon.png",
        server_port=4778,
        theme=gr.themes.Soft(),
        css=UI_CSS,
    )
