import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "../src/App";

const apiMock = vi.hoisted(() => vi.fn());

vi.mock("../src/api", () => ({
  api: apiMock,
  downloadQueryExport: vi.fn(),
  setSessionExpiredHandler: vi.fn(() => () => undefined),
}));

class FakeEventSource {
  static instances: FakeEventSource[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  close = vi.fn();

  constructor() {
    FakeEventSource.instances.push(this);
  }

  emit(data: string) {
    this.onmessage?.({ data });
  }
}

const user = { id: "user-1", email: "admin@local", role: "admin" as const };
const runtime = { per_chip_daily_cap: 80, business_start_hour: 9, business_end_hour: 18, timezone: "America/Sao_Paulo" };
const card = { text: "Card", url: "https://example.com", image_asset_id: null, image_url: null, show_url: true, revision: "rev-1", configured: true, updated_at: null };
const messageGeneration = { api_key_configured: true, model: "gpt-5.6-luna" };
const database = { server_old: "db", database_old: "contacts", username_old: "reader", password_configured: true, configured: true };
const importBatch = { id: "batch-1", filename: "contacts.csv", state: "previewed", total_rows: 1, valid_rows: 1, invalid_rows: 0, duplicate_rows: 0, error: null, created_at: "2026-08-14T12:00:00Z", preview: [] };

beforeEach(() => {
  window.sessionStorage.clear();
  FakeEventSource.instances = [];
  vi.stubGlobal("EventSource", FakeEventSource);
  apiMock.mockImplementation(async (path: string, options?: RequestInit) => {
    if (path === "/auth/me") return user;
    if (path === "/accounts" || path === "/campaigns" || path === "/reviews") return [];
    if (path === "/settings/runtime") return runtime;
    if (path === "/settings/message-card") return card;
    if (path === "/settings/message-generation") return messageGeneration;
    if (path === "/settings/source-database") return database;
    if (path === "/imports/preview" || path === "/imports/batch-1") return importBatch;
    if (path === "/message-variations/generate") {
      const body = JSON.parse(String(options?.body));
      return { original: body.original, variations: Array.from({ length: body.count }, (_, index) => `${body.original} Versão ${index + 1}.`) };
    }
    return undefined;
  });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  apiMock.mockReset();
});

describe("dashboard stability", () => {
  it("ignores heartbeats and restores safe settings drafts after sidebar navigation", async () => {
    render(<App />);
    await screen.findByText("Operação em tempo real");
    fireEvent.click(screen.getByRole("button", { name: "Configurações" }));

    fireEvent.click(await screen.findByRole("tab", { name: /Chip/ }));
    const cap = await screen.findByLabelText("Teto diário por chip");
    fireEvent.change(cap, { target: { value: "125" } });
    fireEvent.click(screen.getByRole("tab", { name: /Consultas/ }));
    const password = screen.getByLabelText("PASSWORD_OLD");
    fireEvent.change(password, { target: { value: "do-not-store" } });
    await waitFor(() => expect(apiMock).toHaveBeenCalledWith("/settings/source-database"));
    const callsBeforeHeartbeat = apiMock.mock.calls.length;

    act(() => FakeEventSource.instances.at(-1)?.emit('{"type":"heartbeat"}'));
    expect(apiMock).toHaveBeenCalledTimes(callsBeforeHeartbeat);

    const accountCalls = apiMock.mock.calls.filter(([path]) => path === "/accounts").length;
    const configurationCalls = apiMock.mock.calls.filter(([path]) => String(path).startsWith("/settings/")).length;
    act(() => FakeEventSource.instances.at(-1)?.emit('{"type":"account.status"}'));
    await waitFor(() => expect(apiMock.mock.calls.filter(([path]) => path === "/accounts").length).toBeGreaterThan(accountCalls));
    expect(apiMock.mock.calls.filter(([path]) => String(path).startsWith("/settings/")).length).toBe(configurationCalls);

    fireEvent.click(screen.getByRole("button", { name: "Visão geral" }));
    fireEvent.click(screen.getByRole("button", { name: "Configurações" }));
    fireEvent.click(await screen.findByRole("tab", { name: /Chip/ }));
    expect(await screen.findByLabelText("Teto diário por chip")).toHaveValue(125);
    fireEvent.click(screen.getByRole("tab", { name: /Consultas/ }));
    expect(screen.getByLabelText("PASSWORD_OLD")).toHaveValue("");
    expect(Object.values(window.sessionStorage).join(" ")).not.toContain("do-not-store");
  });

  it("groups settings into tabs and sends the requested fleet size", async () => {
    apiMock.mockImplementation(async (path: string, options?: RequestInit) => {
      if (path === "/auth/me") return user;
      if (path === "/accounts" || path === "/campaigns" || path === "/reviews") return [];
      if (path === "/settings/runtime") return runtime;
      if (path === "/settings/message-card") return card;
      if (path === "/settings/message-generation") return messageGeneration;
      if (path === "/settings/source-database") return database;
      if (path === "/accounts/bulk" && options?.method === "POST") {
        return Array.from({ length: 42 }, (_, index) => ({ id: `chip-${index + 1}` }));
      }
      return undefined;
    });
    render(<App />);
    await screen.findByText("Operação em tempo real");
    fireEvent.click(screen.getByRole("button", { name: "Configurações" }));

    expect(await screen.findAllByRole("tab")).toHaveLength(4);
    fireEvent.click(screen.getByRole("tab", { name: /^Chip/ }));
    await screen.findByText("Frota inicial");
    const fleetSize = await screen.findByLabelText(/^Quantidade de chips/);
    expect(fleetSize).toHaveValue(30);
    fireEvent.change(fleetSize, { target: { value: "42" } });
    fireEvent.click(screen.getByRole("button", { name: "Ajustar frota para 42 chips" }));

    await waitFor(() => expect(apiMock).toHaveBeenCalledWith(
      "/accounts/bulk",
      expect.objectContaining({ method: "POST", body: JSON.stringify({ count: 42, prefix: "chip" }) }),
    ));
    await screen.findByText(/Frota ajustada para 42 chips/);
  });

  it("restores campaign fields and an uploaded import batch", async () => {
    render(<App />);
    await screen.findByText("Operação em tempo real");
    fireEvent.click(screen.getByRole("button", { name: "Campanhas" }));

    await screen.findByPlaceholderText("Ex.: Cobrança agosto");
    const fileInput = document.querySelector<HTMLInputElement>('input[type="file"][accept=".csv,.xlsx,.xls"]');
    expect(fileInput).not.toBeNull();
    fireEvent.change(fileInput!, { target: { files: [new File(["Telefone"], "contacts.csv", { type: "text/csv" })] } });
    await screen.findByText("contacts.csv");
    fireEvent.change(screen.getByPlaceholderText("Ex.: Cobrança agosto"), { target: { value: "Cobrança estável" } });
    expect(window.sessionStorage.getItem("autowpp:draft:v1:user-1:campaign")).toContain("Cobrança estável");

    fireEvent.click(screen.getByRole("button", { name: "Visão geral" }));
    fireEvent.click(screen.getByRole("button", { name: "Campanhas" }));
    expect(await screen.findByDisplayValue("Cobrança estável")).toBeInTheDocument();
    await screen.findByText("contacts.csv");
    expect(apiMock).toHaveBeenCalledWith("/imports/batch-1");
  });

  it("generates, edits, approves and discards message variations", async () => {
    render(<App />);
    await screen.findByRole("button", { name: "Campanhas" });
    fireEvent.click(screen.getByRole("button", { name: "Campanhas" }));

    fireEvent.click(await screen.findByRole("button", { name: "Criar variações da mensagem" }));
    const slider = screen.getByLabelText("Quantidade de variações");
    expect(slider).toHaveValue("10");
    fireEvent.change(slider, { target: { value: "0" } });
    expect(screen.getByRole("button", { name: "Gerar variações" })).toBeDisabled();
    fireEvent.change(slider, { target: { value: "2" } });
    fireEvent.click(screen.getByRole("button", { name: "Gerar variações" }));

    const first = await screen.findByLabelText("Variação 1");
    const second = screen.getByLabelText("Variação 2");
    expect((first as HTMLTextAreaElement).value).toContain("NOME_DO_CLIENTE");
    expect((second as HTMLTextAreaElement).value).toContain("NOME_DO_CLIENTE");
    fireEvent.change(slider, { target: { value: "3" } });
    fireEvent.change(slider, { target: { value: "2" } });
    expect(screen.getByRole("button", { name: "Usar variações" })).toBeDisabled();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    fireEvent.click(screen.getByRole("button", { name: "Gerar variações" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Usar variações" })).toBeEnabled());
    fireEvent.change(first, { target: { value: "Olá NOME_DO_CLIENTE, esta é a versão revisada." } });
    fireEvent.click(screen.getByRole("button", { name: "Usar variações" }));

    const stored = window.sessionStorage.getItem("autowpp:draft:v1:user-1:campaign") ?? "";
    expect(stored).toContain("versão revisada");
    expect(await screen.findByText("Original + 2 variações")).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Texto"), { target: { value: "Boa tarde NOME_DO_CLIENTE, temos uma informação importante." } });
    expect(await screen.findByText(/O texto original mudou depois da geração/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Criar variações da mensagem" }));
    expect(screen.getByRole("button", { name: "Usar variações" })).toBeEnabled();
    fireEvent.change(screen.getByLabelText("Variação 1"), { target: { value: "Alteração não confirmada NOME_DO_CLIENTE" } });
    fireEvent.click(screen.getByRole("button", { name: "Fechar" }));
    expect(window.sessionStorage.getItem("autowpp:draft:v1:user-1:campaign")).not.toContain("Alteração não confirmada");

    fireEvent.click(screen.getByRole("button", { name: "Criar variações da mensagem" }));
    fireEvent.click(screen.getByRole("button", { name: "Não usar variações" }));
    expect(screen.queryByText("Original + 2 variações")).not.toBeInTheDocument();
    expect(window.sessionStorage.getItem("autowpp:draft:v1:user-1:campaign")).toContain('"message_variations":[]');
  });

  it("keeps the OpenAI key out of session drafts while saving it", async () => {
    render(<App />);
    await screen.findByRole("button", { name: "Configurações" });
    fireEvent.click(screen.getByRole("button", { name: "Configurações" }));
    fireEvent.click(await screen.findByRole("tab", { name: /Mensagens/ }));
    const keyInput = await screen.findByLabelText(/API key da OpenAI/);
    fireEvent.change(keyInput, { target: { value: "sk-test-secret" } });
    fireEvent.click(screen.getByRole("button", { name: "Salvar configuração" }));

    await waitFor(() => expect(apiMock).toHaveBeenCalledWith(
      "/settings/message-generation",
      expect.objectContaining({ method: "PUT", body: JSON.stringify({ api_key: "sk-test-secret" }) }),
    ));
    expect(keyInput).toHaveValue("");
    expect(Object.values(window.sessionStorage).join(" ")).not.toContain("sk-test-secret");
  });
});
