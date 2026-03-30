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
import gradio as gr

import orchestrator

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

    def start(self, count: int):
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

        # Redirect stdout so we capture all print() output from orchestrator
        original_stdout = sys.stdout
        sys.stdout = _TeeWriter(self, original_stdout)
        try:
            orchestrator.main()
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
            updates.append(gr.update(visible=True, value="", label=f"Account {i}  (account_{i})"))
        else:
            updates.append(gr.update(visible=False, value=""))
    return updates


def run_orchestrator(count_str):
    count = int(count_str)

    if runner.running:
        yield _build_outputs("Orchestrator is already running.\n", _empty_logs(count), count, running=True)
        return

    t = threading.Thread(target=runner.start, args=(count,), daemon=True)
    t.start()

    while runner.running or t.is_alive():
        time.sleep(1)
        general, account_logs, cnt = runner.snapshot()
        yield _build_outputs(general, account_logs, cnt, running=True)

    general, account_logs, cnt = runner.snapshot()
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

def build_ui():
    account_boxes = []
    initial_count = len(orchestrator.ACCOUNTS)

    css = """
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

    with gr.Blocks(title="WhatsApp Orchestrator", theme=gr.themes.Soft(), css=css) as demo:
        gr.Markdown("# WhatsApp Multi-Account Orchestrator")

        with gr.Row():
            account_dropdown = gr.Dropdown(
                choices=["1", "2", "3", "4", "5", "6"],
                value=str(initial_count),
                label="Número de Chips",
                interactive=True,
                scale=1,
            )
            run_btn = gr.Button("▶  Run Disparos", variant="primary", scale=3)
            stop_btn = gr.Button("⏹  Stop", variant="stop", interactive=False, scale=1)

        with gr.Accordion("Orchestrator Log", open=False):
            general_box = gr.Textbox(
                label="General Output",
                lines=32,
                max_lines=32,
                interactive=False,
                show_copy_button=True,
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
                        show_copy_button=True,
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

        run_btn.click(
            fn=run_orchestrator,
            inputs=[account_dropdown],
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
    demo.launch(share=False)