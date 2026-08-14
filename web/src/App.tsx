import {
  Activity,
  AlertTriangle,
  Check,
  ChevronRight,
  CirclePause,
  Database,
  Gauge,
  Image as ImageIcon,
  LayoutDashboard,
  LogOut,
  MessageSquareText,
  Play,
  Plus,
  Power,
  RefreshCw,
  RotateCcw,
  Settings,
  Shuffle,
  ShieldCheck,
  Smartphone,
  Upload,
  Users,
  X,
} from "lucide-react";
import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { QRCodeSVG } from "qrcode.react";
import { api, downloadQueryExport, setSessionExpiredHandler } from "./api";
import { createRefreshCoordinator, parseDashboardEvent } from "./dashboard";
import { clearUserSessionDrafts, useSessionDraft } from "./drafts";
import type {
  Account,
  Campaign,
  CampaignDraft,
  Estimate,
  ImportBatch,
  MessageCardDraft,
  MessageCardSettings,
  MessageGenerationSettings,
  MessageVariationResponse,
  NewUserDraft,
  ReviewItem,
  RuntimeSettings,
  RuntimeSettingsDraft,
  SourceDatabaseDraft,
  SourceDatabaseSettings,
  User,
} from "./types";

type View = "overview" | "campaign" | "reviews" | "settings";
type SettingsTab = "messages" | "queries" | "chips" | "users";

const stateLabels: Record<string, string> = {
  offline: "Offline",
  connecting: "Conectando",
  qr_required: "QR necessário",
  ready: "Pronto",
  degraded: "Instável",
  backoff: "Reconectando",
  logged_out: "Desconectado",
  disabled: "Desativado",
};

const campaignLabels: Record<string, string> = {
  draft: "Rascunho",
  scheduled: "Agendada",
  active: "Em andamento",
  awaiting_results: "Aguardando resultados",
  paused: "Pausada",
  completed: "Concluída",
  cancelled: "Cancelada",
};

function formatDate(value: string | null | undefined) {
  if (!value) return "—";
  return new Intl.DateTimeFormat("pt-BR", {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function formatDuration(minutes: number) {
  if (minutes < 60) return `${Math.round(minutes)} min`;
  const hours = Math.floor(minutes / 60);
  const rest = Math.round(minutes % 60);
  return `${hours}h${rest ? ` ${rest}min` : ""}`;
}

function Login({ onLogin }: { onLogin: (user: User) => void }) {
  const [email, setEmail] = useState("admin@local");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      onLogin(
        await api<User>("/auth/login", {
          method: "POST",
          body: JSON.stringify({ email, password }),
        }),
      );
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Não foi possível entrar.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="login-shell">
      <section className="login-panel">
        <div className="brand-mark"><MessageSquareText size={24} /></div>
        <p className="eyebrow">CENTRAL DE ORQUESTRAÇÃO</p>
        <h1>Envios sob controle,<br />chip por chip.</h1>
        <p className="login-copy">
          Acompanhe conexões, distribua campanhas e trate falhas em uma única central.
        </p>
        <form onSubmit={submit}>
          <label>E-mail<input value={email} onChange={(event) => setEmail(event.target.value)} type="email" required /></label>
          <label>Senha<input value={password} onChange={(event) => setPassword(event.target.value)} type="password" required /></label>
          {error && <div className="inline-error"><AlertTriangle size={16} />{error}</div>}
          <button className="button primary full" disabled={busy}>{busy ? "Entrando…" : "Entrar no painel"}<ChevronRight size={18} /></button>
        </form>
      </section>
      <aside className="login-aside">
        <div className="network-orbit">
          <span className="orbit-center"><Activity /></span>
          {Array.from({ length: 12 }).map((_, index) => <i key={index} style={{ "--i": index } as React.CSSProperties} />)}
        </div>
        <div><strong>30+ chips</strong><span>2 nodos interconectados</span></div>
        <div><strong>Sistema Inteligente</strong><span>sessões por QR independentes</span></div>
      </aside>
    </main>
  );
}

function StatCard({ icon, label, value, detail, tone = "default" }: { icon: React.ReactNode; label: string; value: string | number; detail: string; tone?: string }) {
  return <article className={`stat-card ${tone}`}><span className="stat-icon">{icon}</span><div><p>{label}</p><strong>{value}</strong><small>{detail}</small></div></article>;
}

function AccountCard({ account, onQr, onConnect, onDisconnect, onReset }: { account: Account; onQr: () => void; onConnect: () => void; onDisconnect: () => void; onReset: () => void }) {
  return (
    <article className={`account-card state-${account.state}`}>
      <header><span className="account-index">{account.display_name.replace(/\D/g, "").padStart(2, "0") || "•"}</span><span className={`state-pill ${account.state}`}><i />{stateLabels[account.state]}</span></header>
      <h3>{account.display_name}</h3>
      <p>{account.phone || "Número ainda não identificado"}</p>
      <dl>
        <div><dt>Nó</dt><dd>{account.node_id || "—"}</dd></div>
        <div><dt>Hoje</dt><dd>{account.sent_today}</dd></div>
        <div><dt>Heartbeat</dt><dd>{formatDate(account.last_heartbeat_at)}</dd></div>
      </dl>
      {account.last_error && <small className="account-error" title={account.last_error}>{account.last_error}</small>}
      <footer>
        {account.qr_code && <button className="link-button" onClick={onQr}>Exibir QR</button>}
        {account.state === "disabled" && <button className="link-button" onClick={onConnect}><Power size={12} />Ativar</button>}
        {["offline", "degraded", "backoff", "logged_out"].includes(account.state) && <button className="link-button" onClick={onConnect}>Reconectar</button>}
        {["offline", "degraded", "backoff", "logged_out"].includes(account.state) && <button className="link-button danger-text" title="Desativar chip" aria-label={`Desativar ${account.display_name}`} onClick={onDisconnect}><Power size={13} />Desativar</button>}
        {["connecting", "qr_required"].includes(account.state) && <button className="link-button danger-text" onClick={onDisconnect}>Cancelar ativação</button>}
        {account.state === "ready" && <button className="link-button danger-text" onClick={onDisconnect}>Desconectar chip</button>}
        {account.state !== "disabled" && <button className="link-button" onClick={onReset}><RotateCcw size={12} />Refazer sessão</button>}
      </footer>
    </article>
  );
}

function Overview({ accounts, campaigns, settings, refresh, onQr, onConnect, onDisconnect, onReset }: { accounts: Account[]; campaigns: Campaign[]; settings: RuntimeSettings | null; refresh: () => void; onQr: (account: Account) => void; onConnect: (account: Account) => void; onDisconnect: (account: Account) => void; onReset: (account: Account) => void }) {
  const ready = accounts.filter((account) => account.state === "ready").length;
  const issues = accounts.filter((account) => !["ready", "connecting"].includes(account.state)).length;
  const active = campaigns.find((campaign) => ["active", "awaiting_results"].includes(campaign.state));
  const sent = accounts.reduce((sum, account) => sum + account.sent_today, 0);
  const dailyCapacity = ready * (settings?.per_chip_daily_cap ?? 0);
  const remainingCapacity = accounts.filter((account) => account.state === "ready").reduce((sum, account) => sum + Math.max(0, (settings?.per_chip_daily_cap ?? 0) - account.sent_today), 0);
  return (
    <div className="view-stack">
      <section className="page-heading"><div><p className="eyebrow">VISÃO GERAL</p><h1>Operação em tempo real</h1><p>Saúde das sessões e ritmo do disparo em uma única leitura.</p></div><button className="button secondary" onClick={refresh}><RefreshCw size={16} />Atualizar</button></section>
      <section className="stats-grid">
        <StatCard icon={<Smartphone />} label="Chips prontos" value={`${ready}/${accounts.length || 30}`} detail={issues ? `${issues} exigem atenção` : "Todos operacionais"} tone={issues ? "warning" : "success"} />
        <StatCard icon={<MessageSquareText />} label="Enviadas hoje" value={sent.toLocaleString("pt-BR")} detail="Somatório de todos os chips" />
        <StatCard icon={<Activity />} label="Campanha ativa" value={active ? `${active.sent}/${active.total}` : "Nenhuma"} detail={active?.name || "Fila disponível"} />
        <StatCard icon={<Gauge />} label="Capacidade diária" value={dailyCapacity ? `${dailyCapacity}/dia` : "0"} detail={`${remainingCapacity} envios restantes hoje`} />
      </section>
      {active && <section className={`active-strip ${active.state}`}><div><span className="pulse" /><div><small>{active.state === "awaiting_results" ? "AGUARDANDO RECIBOS REAIS" : "EM ANDAMENTO"}</small><strong>{active.name}</strong></div></div><div className="strip-progress"><span style={{ width: `${active.total ? (active.sent / active.total) * 100 : 0}%` }} /></div><div><strong>{active.total ? Math.round((active.sent / active.total) * 100) : 0}%</strong><small>{active.state === "awaiting_results" ? "Resultado final em até 2 min" : `Previsão ${formatDate(active.estimated_finish_at)}`}</small></div></section>}
      <section className="section-heading"><div><h2>Frota de chips</h2><p>{ready} prontos · {accounts.filter((a) => a.state === "qr_required").length} aguardando QR</p></div></section>
      <section className="accounts-grid">
        {accounts.map((account) => <AccountCard key={account.id} account={account} onQr={() => onQr(account)} onConnect={() => onConnect(account)} onDisconnect={() => onDisconnect(account)} onReset={() => onReset(account)} />)}
        {!accounts.length && <div className="empty-state"><Smartphone /><strong>Nenhum chip cadastrado</strong><span>Um administrador pode definir a frota inicial em Configurações.</span></div>}
      </section>
    </div>
  );
}

function NativeLinkPreview({ url, title, imageUrl, showUrl, message = "Mensagem personalizada da campanha" }: { url: string; title: string; imageUrl: string | null; showUrl: boolean; message?: string }) {
  let host = "Site de destino";
  try { if (url) host = new URL(url).hostname; } catch { /* The form/API displays validation. */ }
  return <div className="message-card-preview">
    <div className="native-link-card">
      {imageUrl ? <img src={imageUrl} alt="Imagem personalizada do card" /> : <div className="message-card-placeholder"><ImageIcon /></div>}
      <div><small>PRÉVIA NATIVA PERSONALIZADA</small><strong>{title || "Texto do card"}</strong><span>{host}</span></div>
    </div>
    <div className="message-caption-preview">
      {showUrl && <span>{url || "https://destino.example"}</span>}
      <p>{message}</p>
      {!showUrl && <div className="interactive-link-button">Acessar</div>}
    </div>
  </div>;
}

const emptyCampaignDraft: CampaignDraft = {
  version: 1,
  batch_id: null,
  interval_mean_minutes: 0,
  name: "",
  message_variations: [],
  message_variations_source: "",
  message: "Bom dia NOME_DO_CLIENTE, temos uma informação importante para você.",
};

const messagePlaceholders = ["NOME_DO_CLIENTE", "CREDOR"] as const;

function validateVariationTexts(original: string, variations: string[]) {
  const seen = new Set([original.trim()]);
  for (let index = 0; index < variations.length; index += 1) {
    const variation = variations[index].trim();
    if (!variation) return `A Variação ${index + 1} não pode ficar vazia.`;
    for (const placeholder of messagePlaceholders) {
      const expected = original.split(placeholder).length - 1;
      const actual = variation.split(placeholder).length - 1;
      if (actual !== expected) return `A Variação ${index + 1} precisa preservar exatamente as ocorrências de ${placeholder}.`;
    }
    if (seen.has(variation)) return `A Variação ${index + 1} está duplicada.`;
    seen.add(variation);
  }
  return "";
}

function CampaignBuilder({ userId, accounts, campaigns, settings, messageCard, messageGeneration, refresh }: { userId: string; accounts: Account[]; campaigns: Campaign[]; settings: RuntimeSettings | null; messageCard: MessageCardSettings | null; messageGeneration: MessageGenerationSettings | null; refresh: () => void }) {
  const campaignDraft = useSessionDraft(userId, "campaign", emptyCampaignDraft);
  const { interval_mean_minutes: interval, name, message } = campaignDraft.value;
  const approvedVariations = campaignDraft.value.message_variations ?? [];
  const approvedVariationSource = campaignDraft.value.message_variations_source ?? "";
  const [batch, setBatch] = useState<ImportBatch | null>(null);
  const [estimate, setEstimate] = useState<Estimate | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [reviewOpen, setReviewOpen] = useState(false);
  const [variationsOpen, setVariationsOpen] = useState(false);
  const [variationCount, setVariationCount] = useState(10);
  const [workingVariations, setWorkingVariations] = useState<string[]>([]);
  const [workingVariationSource, setWorkingVariationSource] = useState("");
  const [generatedCount, setGeneratedCount] = useState<number | null>(null);
  const [variationSelectionStale, setVariationSelectionStale] = useState(false);
  const [variationBusy, setVariationBusy] = useState(false);
  const [variationError, setVariationError] = useState("");

  useEffect(() => {
    if (!variationsOpen) return;
    const closeOnEscape = (event: KeyboardEvent) => { if (event.key === "Escape" && !variationBusy) setVariationsOpen(false); };
    document.addEventListener("keydown", closeOnEscape);
    return () => document.removeEventListener("keydown", closeOnEscape);
  }, [variationsOpen, variationBusy]);

  function openVariationModal() {
    setWorkingVariations([...approvedVariations]);
    setWorkingVariationSource(approvedVariationSource || message);
    setVariationCount(approvedVariations.length || 10);
    setGeneratedCount(approvedVariations.length || null);
    setVariationSelectionStale(false);
    setVariationError("");
    setVariationsOpen(true);
  }

  async function generateVariations() {
    if (!variationCount || variationBusy) return;
    if (workingVariations.length && !window.confirm("Gerar novamente substituirá as variações exibidas. Continuar?")) return;
    setVariationBusy(true); setVariationError("");
    try {
      const generated = await api<MessageVariationResponse>("/message-variations/generate", { method: "POST", body: JSON.stringify({ original: message, count: variationCount }) });
      setWorkingVariations(generated.variations);
      setWorkingVariationSource(generated.original);
      setGeneratedCount(variationCount);
      setVariationSelectionStale(false);
    } catch (cause) {
      setVariationError(cause instanceof Error ? cause.message : "Não foi possível gerar as variações.");
    } finally { setVariationBusy(false); }
  }

  function useVariations() {
    const validationError = validateVariationTexts(message, workingVariations);
    if (validationError) { setVariationError(validationError); return; }
    campaignDraft.setValue((current) => ({ ...current, message_variations: workingVariations.map((item) => item.trim()), message_variations_source: workingVariationSource }));
    setVariationsOpen(false);
  }

  function discardVariations() {
    campaignDraft.setValue((current) => ({ ...current, message_variations: [], message_variations_source: "" }));
    setVariationsOpen(false);
  }

  useEffect(() => {
    if (!campaignDraft.value.batch_id || batch?.id === campaignDraft.value.batch_id) return;
    let cancelled = false;
    api<ImportBatch>(`/imports/${campaignDraft.value.batch_id}`)
      .then((restored) => { if (!cancelled) setBatch(restored); })
      .catch((cause) => {
        if (cancelled) return;
        campaignDraft.setValue((current) => ({ ...current, batch_id: null }));
        setError(cause instanceof Error ? cause.message : "A importação anterior não está mais disponível.");
      });
    return () => { cancelled = true; };
  }, [batch?.id, campaignDraft.value.batch_id, campaignDraft.setValue]);

  useEffect(() => {
    if (!batch || !settings?.per_chip_daily_cap) return;
    let cancelled = false;
    const timer = window.setTimeout(() => {
      api<Estimate>("/campaigns/estimate", { method: "POST", body: JSON.stringify({ import_id: batch.id, interval_mean_minutes: interval }) })
        .then((nextEstimate) => { if (!cancelled) { setEstimate(nextEstimate); setError(""); } })
        .catch((cause) => { if (!cancelled) { setEstimate(null); setError(cause.message); } });
    }, 250);
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [batch, interval, settings?.per_chip_daily_cap, accounts]);

  async function upload(file: File) {
    setBusy(true); setError(""); setNotice("");
    const data = new FormData(); data.append("file", file);
    try {
      const result = await api<ImportBatch>("/imports/preview", { method: "POST", body: data });
      setBatch(result);
      campaignDraft.setValue((current) => ({ ...current, batch_id: result.id, name: file.name.replace(/\.[^.]+$/, "") }));
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Falha no upload"); }
    finally { setBusy(false); }
  }

  async function confirmAndStart() {
    if (!batch) return;
    setBusy(true); setError("");
    try {
      await api<Campaign>("/campaigns/confirm-and-start", { method: "POST", body: JSON.stringify({ name, import_id: batch.id, message, message_variations: approvedVariations, card_revision: messageCard?.revision ?? "", interval_mean_minutes: interval, confirmed_real_send: true }) });
      setReviewOpen(false); setNotice("Campanha real confirmada e agendada com sucesso."); setBatch(null); setEstimate(null); campaignDraft.clear(emptyCampaignDraft); refresh();
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Falha ao criar campanha"); }
    finally { setBusy(false); }
  }

  async function campaignAction(id: string, action: string) {
    try { await api(`/campaigns/${id}/${action}`, { method: "POST" }); refresh(); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "Falha na ação"); }
  }

  async function exportQuery() {
    setBusy(true); setError("");
    try { await downloadQueryExport(); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "Falha ao gerar arquivo"); }
    finally { setBusy(false); }
  }

  const workingValidationError = workingVariations.length ? validateVariationTexts(message, workingVariations) : "";
  const generatedForCurrentCount = variationCount > 0 && !variationSelectionStale && generatedCount === variationCount && workingVariations.length === variationCount;

  return <div className="view-stack">
    <section className="page-heading"><div><p className="eyebrow">CAMPANHAS</p><h1>Preparar novo disparo</h1><p>Valide os dados, ajuste a cadência e confira a previsão antes de iniciar.</p></div><button className="button secondary" onClick={exportQuery} disabled={busy}><Database size={16} />Gerar arquivo da query</button></section>
    {!settings?.per_chip_daily_cap && <div className="banner warning"><AlertTriangle /><div><strong>Teto diário por chip ainda não configurado</strong><span>Um administrador precisa definir o limite antes da primeira campanha.</span></div></div>}
    {!messageCard?.configured && <div className="banner warning"><AlertTriangle /><div><strong>Card nativo ainda não configurado</strong><span>Um administrador precisa definir texto, imagem e link antes de iniciar uma campanha.</span></div></div>}
    {error && <div className="banner danger"><AlertTriangle /><div><strong>Não foi possível concluir</strong><span>{error}</span></div><button onClick={() => setError("")}><X /></button></div>}
    {notice && <div className="banner success"><Check /><div><strong>Campanha pronta</strong><span>{notice}</span></div></div>}
    <section className="builder-grid">
      <article className="panel upload-panel">
        <div className="step-heading"><span>01</span><div><h2>Base de contatos</h2><p>CSV ou XLSX com até 20 MB</p></div></div>
        <label className={`drop-zone ${batch ? "has-file" : ""}`}>
          <input type="file" accept=".csv,.xlsx,.xls" onChange={(event) => event.target.files?.[0] && upload(event.target.files[0])} />
          {batch ? <><Check /><strong>{batch.filename}</strong><span>{batch.total_rows} linhas analisadas</span></> : <><Upload /><strong>{busy ? "Processando…" : "Arraste ou selecione a planilha"}</strong><span>Telefone, Nome, pessoaId, Credor e Campanha</span></>}
        </label>
        {batch && <div className="validation-row"><div className="valid"><strong>{batch.valid_rows}</strong><span>válidas</span></div><div><strong>{batch.duplicate_rows}</strong><span>duplicadas</span></div><div className="invalid"><strong>{batch.invalid_rows}</strong><span>inválidas</span></div></div>}
      </article>
      <article className="panel message-panel">
        <div className="step-heading"><span>02</span><div><h2>Mensagem</h2><p>Uma versão nesta primeira etapa</p></div></div>
        <label>Nome da campanha<input value={name} onChange={(event) => campaignDraft.setValue((current) => ({ ...current, name: event.target.value }))} placeholder="Ex.: Cobrança agosto" /></label>
        <div className="message-text-field">
          <div className="message-text-heading"><label htmlFor="campaign-message">Texto</label><button type="button" className="variation-trigger" aria-label="Criar variações da mensagem" title="Criar variações da mensagem" onClick={openVariationModal}><Shuffle size={18} /></button>{approvedVariations.length > 0 && <span className="variation-badge">Original + {approvedVariations.length} variações</span>}</div>
          <textarea id="campaign-message" value={message} onChange={(event) => campaignDraft.setValue((current) => ({ ...current, message: event.target.value }))} rows={5} />
          <small>Use NOME_DO_CLIENTE para inserir o primeiro e o último nomes e CREDOR para inserir o credor da planilha.</small>
          {approvedVariations.length > 0 && approvedVariationSource !== message && <div className="variation-stale"><AlertTriangle size={15} />O texto original mudou depois da geração. As variações aprovadas foram mantidas; revise-as antes do envio.</div>}
        </div>
      </article>
    </section>
    <section className="panel cadence-panel">
      <div className="step-heading"><span>03</span><div><h2>Cadência e previsão</h2><p>O intervalo é aplicado individualmente a cada chip.</p></div></div>
      <div className="slider-layout">
        <div className="slider-control"><div className="slider-value"><strong>{interval === 0 ? "Automático" : `${interval} min`}</strong><span>intervalo médio</span></div><input type="range" min="0" max="30" step="0.5" value={interval} onChange={(event) => campaignDraft.setValue((current) => ({ ...current, interval_mean_minutes: Number(event.target.value) }))} /><div className="range-labels"><span>Auto</span><span>15 min</span><span>30 min</span></div></div>
        <div className="forecast-grid"><div><span>Contatos</span><strong>{estimate?.valid_contacts ?? batch?.valid_rows ?? "—"}</strong></div><div><span>Chips saudáveis</span><strong>{estimate?.healthy_accounts ?? accounts.filter((a) => a.state === "ready").length}</strong></div><div><span>Duração prevista</span><strong>{estimate ? formatDuration(estimate.duration_minutes) : "—"}</strong></div><div className="finish"><span>Conclusão estimada</span><strong>{formatDate(estimate?.estimated_finish_at)}</strong></div></div>
      </div>
      {estimate?.warnings.map((warning) => <div className="estimate-warning" key={warning}><AlertTriangle size={16} />{warning}</div>)}
      <div className="builder-actions"><span>Jitter de 70% a 130% · teto de {settings?.per_chip_daily_cap ?? "não definido"} por chip/dia</span><button className="button primary" disabled={!batch || !name || !message || !settings?.per_chip_daily_cap || !messageCard?.configured || !estimate?.healthy_accounts || busy} onClick={() => setReviewOpen(true)}><ShieldCheck size={17} />Revisar disparo</button></div>
    </section>
    {batch?.preview.length ? <section className="panel preview-panel"><div className="section-heading"><div><h2>Prévia da importação</h2><p>Primeiras {batch.preview.length} linhas</p></div></div><div className="table-wrap"><table><thead><tr><th>Linha</th><th>Nome</th><th>Telefone</th><th>Credor</th><th>Campanha</th><th>Status</th></tr></thead><tbody>{batch.preview.map((row) => <tr key={row.id}><td>{row.row_number}</td><td>{row.raw_data.Nome || row.raw_data.nome || "—"}</td><td>{row.normalized_phone || row.raw_data.Telefone}</td><td>{row.raw_data.Credor || "—"}</td><td>{row.raw_data.Campanha || "—"}</td><td><span className={`row-status ${row.valid ? "ok" : "error"}`}>{row.valid ? "Válida" : row.validation_error}</span></td></tr>)}</tbody></table></div></section> : null}
    <section className="panel"><div className="section-heading"><div><h2>Histórico de campanhas</h2><p>Últimas operações</p></div></div><div className="campaign-list">{campaigns.map((campaign) => <article key={campaign.id}><div><span className={`campaign-state ${campaign.state}`}>{campaignLabels[campaign.state] || campaign.state}</span><h3>{campaign.name}</h3><small>{campaign.sent}/{campaign.total} enviadas · {campaign.delivered} entregues · {campaign.failed} falharam · {campaign.review_required} sem confirmação</small></div><div className="campaign-progress"><span style={{ width: `${campaign.total ? (campaign.sent / campaign.total) * 100 : 0}%` }} /></div><div className="campaign-actions">{campaign.state === "draft" && <button onClick={() => campaignAction(campaign.id, "start")}><Play /></button>}{campaign.state === "active" && <button onClick={() => campaignAction(campaign.id, "pause")}><CirclePause /></button>}{campaign.state === "paused" && <button onClick={() => campaignAction(campaign.id, "resume")}><Play /></button>}{!["completed", "cancelled", "awaiting_results"].includes(campaign.state) && <button className="danger-icon" onClick={() => campaignAction(campaign.id, "cancel")}><X /></button>}</div></article>)}</div></section>
    {variationsOpen && <div className="modal-backdrop"><section className="confirm-modal variation-modal" role="dialog" aria-modal="true" aria-labelledby="variation-modal-title"><button type="button" className="modal-close" aria-label="Fechar" disabled={variationBusy} onClick={() => setVariationsOpen(false)}><X /></button><p className="eyebrow">VARIEDADE DE TEXTO</p><h2 id="variation-modal-title">Gerar variações da mensagem</h2><p className="variation-intro">Crie versões leves em português. O card não será alterado e o texto original também participa do sorteio.</p><div className="variation-slider"><div><label htmlFor="variation-count">Quantidade de variações</label><strong>{variationCount}</strong></div><input id="variation-count" type="range" min="0" max="20" step="1" value={variationCount} disabled={variationBusy} onChange={(event) => { setVariationCount(Number(event.target.value)); if (generatedCount !== null) setVariationSelectionStale(true); setVariationError(""); }} /><div className="range-labels"><span>0</span><span>10</span><span>20</span></div></div>{!messageGeneration?.api_key_configured && <div className="banner warning compact"><AlertTriangle /><div><strong>API key não configurada</strong><span>Um administrador precisa cadastrá-la em Configurações → Mensagens.</span></div></div>}<button type="button" className="button primary variation-generate" disabled={variationCount === 0 || variationBusy || !messageGeneration?.api_key_configured} onClick={() => void generateVariations()}><Shuffle size={17} />{variationBusy ? "Gerando variações…" : "Gerar variações"}</button>{variationSelectionStale && <div className="variation-stale"><AlertTriangle size={15} />A quantidade mudou. Gere novamente antes de usar as variações.</div>}{workingVariations.length > 0 && workingVariationSource !== message && <div className="variation-stale"><AlertTriangle size={15} />O texto original mudou após esta geração. Você ainda pode revisar e usar as versões abaixo.</div>}{variationError && <div className="inline-error"><AlertTriangle size={16} />{variationError}</div>}{!variationError && workingValidationError && <div className="inline-error"><AlertTriangle size={16} />{workingValidationError}</div>}<div className="variation-list">{workingVariations.map((variation, index) => <label key={index}>Variação {index + 1}<textarea aria-label={`Variação ${index + 1}`} value={variation} rows={3} disabled={variationBusy} onChange={(event) => { const next = [...workingVariations]; next[index] = event.target.value; setWorkingVariations(next); setVariationError(""); }} /></label>)}</div><div className="confirm-actions variation-actions"><button type="button" className="button ghost danger-text" disabled={variationBusy} onClick={discardVariations}>Não usar variações</button><button type="button" className="button primary" disabled={variationBusy || !generatedForCurrentCount || Boolean(workingValidationError)} onClick={useVariations}>Usar variações</button></div></section></div>}
    {reviewOpen && batch && estimate && messageCard?.configured && <div className="modal-backdrop"><section className="confirm-modal"><button className="modal-close" onClick={() => setReviewOpen(false)}><X /></button><p className="eyebrow">ENVIO REAL</p><h2>Confirme antes de disparar</h2><div className="confirm-summary"><div><span>Arquivo</span><strong>{batch.filename}</strong></div><div><span>Linhas analisadas</span><strong>{batch.total_rows}</strong></div><div><span>Mensagens reais</span><strong>{batch.valid_rows}</strong></div><div><span>Duplicadas descartadas</span><strong>{batch.duplicate_rows}</strong></div><div><span>Inválidas descartadas</span><strong>{batch.invalid_rows}</strong></div><div><span>Chips prontos</span><strong>{estimate.healthy_accounts}</strong></div><div><span>Teto</span><strong>{estimate.per_chip_daily_cap} por chip/dia</strong></div><div><span>Capacidade restante hoje</span><strong>{estimate.remaining_capacity_today}</strong></div></div><div className="message-review"><span>Card nativo personalizado e texto · {approvedVariations.length ? `Original + ${approvedVariations.length} variações` : "Somente original"}</span><NativeLinkPreview url={messageCard.url} title={messageCard.text} imageUrl={messageCard.image_url} showUrl={messageCard.show_url} message={message} /></div><div className="confirm-warning"><AlertTriangle />Esta ação enviará mensagens pelo WhatsApp real.</div><div className="confirm-actions"><button className="button secondary" onClick={() => setReviewOpen(false)}>Cancelar</button><button className="button primary" disabled={busy} onClick={confirmAndStart}><Play />Confirmar e iniciar {batch.valid_rows} mensagens reais</button></div></section></div>}
  </div>;
}

function Reviews({ items, refresh }: { items: ReviewItem[]; refresh: () => void }) {
  const [error, setError] = useState("");
  async function decide(id: string, action: string) { try { await api(`/reviews/${id}`, { method: "POST", body: JSON.stringify({ action }) }); refresh(); } catch (cause) { setError(cause instanceof Error ? cause.message : "Falha"); } }
  return <div className="view-stack"><section className="page-heading"><div><p className="eyebrow">RESULTADOS</p><h1>Envios que exigem atenção</h1><p>Falhas são reais; ausência de recibo é separada e nunca contada como entrega.</p></div></section>{error && <div className="banner danger"><AlertTriangle />{error}</div>}<section className="panel review-list">{items.length ? items.map((item) => <article key={item.id} className={item.state}><AlertTriangle /><div><strong>{item.phone} · {item.state === "failed" ? "Falha no envio" : "Entrega não confirmada"}</strong><span>{item.account || "Chip não identificado"} · iniciado {formatDate(item.started_at)}</span><small>{item.last_error}</small></div><div><button className="button secondary" onClick={() => decide(item.id, "retry")}>Autorizar retry</button><button className="button ghost danger-text" onClick={() => decide(item.id, "cancel")}>Cancelar</button></div></article>) : <div className="empty-state"><ShieldCheck /><strong>Nenhuma falha ou resultado pendente</strong><span>A fila está limpa.</span></div>}</section></div>;
}

const defaultRuntimeDraft: RuntimeSettingsDraft = { version: 1, per_chip_daily_cap: null, business_start_hour: 9, business_end_hour: 18, timezone: "America/Sao_Paulo" };
const emptyDatabaseDraft: SourceDatabaseDraft = { version: 1, server_old: "", database_old: "", username_old: "" };
const emptyCardDraft: MessageCardDraft = { version: 1, text: "", url: "", show_url: true };
const emptyNewUserDraft: NewUserDraft = { version: 1, email: "", role: "operator" };

function SettingsView({ settings, sourceDatabase, messageCard, messageGeneration, user, accountCount, refreshConfiguration, refreshOperational }: { settings: RuntimeSettings | null; sourceDatabase: SourceDatabaseSettings | null; messageCard: MessageCardSettings | null; messageGeneration: MessageGenerationSettings | null; user: User; accountCount: number; refreshConfiguration: () => Promise<void>; refreshOperational: () => Promise<void> }) {
  const runtimeDraft = useSessionDraft<RuntimeSettingsDraft>(user.id, "settings-runtime", settings ? { version: 1, ...settings } : defaultRuntimeDraft);
  const databaseDraft = useSessionDraft<SourceDatabaseDraft>(user.id, "settings-database", sourceDatabase ? { version: 1, server_old: sourceDatabase.server_old, database_old: sourceDatabase.database_old, username_old: sourceDatabase.username_old } : emptyDatabaseDraft);
  const cardDraft = useSessionDraft<MessageCardDraft>(user.id, "settings-card", messageCard ? { version: 1, text: messageCard.text, url: messageCard.url, show_url: messageCard.show_url } : emptyCardDraft);
  const newUserDraft = useSessionDraft<NewUserDraft>(user.id, "settings-new-user", emptyNewUserDraft);
  const [notice, setNotice] = useState("");
  const [databasePassword, setDatabasePassword] = useState("");
  const [openAIKey, setOpenAIKey] = useState("");
  const [cardImage, setCardImage] = useState<File | null>(null);
  const [cardPreview, setCardPreview] = useState<string | null>(messageCard?.image_url ?? null);
  const cardObjectUrl = useRef<string | null>(null);
  const [newUserPassword, setNewUserPassword] = useState("");
  const [passwords, setPasswords] = useState({ current_password: "", new_password: "" });
  const [activeTab, setActiveTab] = useState<SettingsTab>("messages");
  const [fleetSize, setFleetSize] = useState(accountCount || 30);
  const form = runtimeDraft.value;
  const databaseForm = databaseDraft.value;
  const cardForm = cardDraft.value;
  const newUser = newUserDraft.value;

  useEffect(() => { if (settings) runtimeDraft.hydrate({ version: 1, ...settings }); }, [settings, runtimeDraft.hydrate]);
  useEffect(() => {
    if (!sourceDatabase) return;
    databaseDraft.hydrate({ version: 1, server_old: sourceDatabase.server_old, database_old: sourceDatabase.database_old, username_old: sourceDatabase.username_old });
  }, [sourceDatabase, databaseDraft.hydrate]);
  useEffect(() => {
    if (!messageCard) return;
    cardDraft.hydrate({ version: 1, text: messageCard.text, url: messageCard.url, show_url: messageCard.show_url });
    if (!cardDraft.dirty && !cardImage) setCardPreview(messageCard.image_url);
  }, [messageCard, cardDraft.hydrate, cardDraft.dirty, cardImage]);
  useEffect(() => () => { if (cardObjectUrl.current) URL.revokeObjectURL(cardObjectUrl.current); }, []);

  async function save(event: FormEvent) {
    event.preventDefault();
    try {
      const saved = await api<RuntimeSettings>("/settings/runtime", { method: "PUT", body: JSON.stringify({ per_chip_daily_cap: form.per_chip_daily_cap, business_start_hour: form.business_start_hour, business_end_hour: form.business_end_hour, timezone: form.timezone }) });
      runtimeDraft.clear({ version: 1, ...saved });
      setNotice("Configurações atualizadas.");
      await refreshConfiguration();
    } catch (cause) { setNotice(cause instanceof Error ? cause.message : "Falha"); }
  }
  async function saveDatabase(event: FormEvent) {
    event.preventDefault();
    try {
      const saved = await api<SourceDatabaseSettings>("/settings/source-database", { method: "PUT", body: JSON.stringify({ server_old: databaseForm.server_old, database_old: databaseForm.database_old, username_old: databaseForm.username_old, password_old: databasePassword }) });
      databaseDraft.clear({ version: 1, server_old: saved.server_old, database_old: saved.database_old, username_old: saved.username_old });
      setDatabasePassword("");
      setNotice("Credenciais da consulta atualizadas.");
      await refreshConfiguration();
    } catch (cause) { setNotice(cause instanceof Error ? cause.message : "Falha ao salvar as credenciais"); }
  }
  async function saveMessageGeneration(event: FormEvent) {
    event.preventDefault();
    try {
      await api<MessageGenerationSettings>("/settings/message-generation", { method: "PUT", body: JSON.stringify({ api_key: openAIKey }) });
      setOpenAIKey("");
      setNotice("Configuração da geração de variações atualizada.");
      await refreshConfiguration();
    } catch (cause) { setNotice(cause instanceof Error ? cause.message : "Falha ao salvar a API key"); }
  }
  async function syncFleet(event: FormEvent) {
    event.preventDefault();
    if (fleetSize < accountCount && !window.confirm(`Reduzir a frota para ${fleetSize} chips apagará as sessões e configurações dos chips ${fleetSize + 1} a ${accountCount}. Continuar?`)) return;
    try {
      const accounts = await api<Account[]>("/accounts/bulk", { method: "POST", body: JSON.stringify({ count: fleetSize, prefix: "chip" }) });
      setFleetSize(accounts.length);
      setNotice(`Frota ajustada para ${accounts.length} chips. Chips novos aguardam cadastro e leitura do QR.`);
      await refreshOperational();
    } catch (cause) { setNotice(cause instanceof Error ? cause.message : "Falha ao ajustar a frota"); }
  }
  async function createUser(event: FormEvent) { event.preventDefault(); try { await api("/auth/users", { method: "POST", body: JSON.stringify({ email: newUser.email, role: newUser.role, password: newUserPassword }) }); newUserDraft.clear(emptyNewUserDraft); setNewUserPassword(""); setNotice("Usuário criado."); } catch (cause) { setNotice(cause instanceof Error ? cause.message : "Falha"); } }
  async function changePassword(event: FormEvent) { event.preventDefault(); try { await api("/auth/change-password", { method: "POST", body: JSON.stringify(passwords) }); setPasswords({ current_password: "", new_password: "" }); setNotice("Senha alterada."); } catch (cause) { setNotice(cause instanceof Error ? cause.message : "Falha"); } }
  async function saveCard(event: FormEvent) {
    event.preventDefault();
    const data = new FormData();
    data.append("text", cardForm.text);
    data.append("url", cardForm.url);
    data.append("show_url", String(cardForm.show_url));
    if (cardImage) data.append("image", cardImage);
    try {
      const saved = await api<MessageCardSettings>("/settings/message-card", { method: "PUT", body: data });
      cardDraft.clear({ version: 1, text: saved.text, url: saved.url, show_url: saved.show_url });
      if (cardObjectUrl.current) URL.revokeObjectURL(cardObjectUrl.current);
      cardObjectUrl.current = null;
      setCardImage(null); setCardPreview(saved.image_url); setNotice("Card nativo personalizado atualizado."); await refreshConfiguration();
    } catch (cause) { setNotice(cause instanceof Error ? cause.message : "Falha ao salvar o card"); }
  }
  function selectCardImage(file: File | undefined) {
    if (!file) return;
    if (cardObjectUrl.current) URL.revokeObjectURL(cardObjectUrl.current);
    cardObjectUrl.current = URL.createObjectURL(file);
    setCardImage(file);
    setCardPreview(cardObjectUrl.current);
  }
  if (user.role !== "admin") return <div className="empty-state full-page"><ShieldCheck /><strong>Acesso restrito</strong><span>Somente administradores alteram parâmetros operacionais.</span></div>;
  const tabs: Array<{ id: SettingsTab; label: string; description: string }> = [
    { id: "messages", label: "Mensagens", description: "Card nativo personalizado" },
    { id: "queries", label: "Consultas", description: "Banco da consulta" },
    { id: "chips", label: "Chip", description: "Cadência e frota inicial" },
    { id: "users", label: "Usuários", description: "Acessos e senha" },
  ];
  return <div className="view-stack">
    <section className="page-heading"><div><p className="eyebrow">ADMINISTRAÇÃO</p><h1>Parâmetros de operação</h1><p>Limites, janela comercial, frota e acesso.</p></div><button className="button secondary" type="button" onClick={() => void refreshConfiguration()}><RefreshCw size={16} />Atualizar</button></section>
    {notice && <div className="banner neutral"><Check />{notice}</div>}
    <nav className="settings-tabs" role="tablist" aria-label="Categorias de configurações">
      {tabs.map((tab) => <button key={tab.id} id={`settings-tab-${tab.id}`} type="button" role="tab" aria-selected={activeTab === tab.id} aria-controls={`settings-panel-${tab.id}`} className={activeTab === tab.id ? "active" : ""} onClick={() => setActiveTab(tab.id)}><strong>{tab.label}</strong><span>{tab.description}</span></button>)}
    </nav>
    <section className="settings-tab-panel" id={`settings-panel-${activeTab}`} role="tabpanel" aria-labelledby={`settings-tab-${activeTab}`}>
      {activeTab === "messages" && <div className="settings-grid">
        <form className="panel message-card-settings" onSubmit={saveCard}>
        <div className="step-heading"><span><MessageSquareText /></span><div><h2>Card nativo personalizado</h2><p>Prévia de link com texto e imagem definidos por você</p></div></div>
        <NativeLinkPreview url={cardForm.url} title={cardForm.text} imageUrl={cardPreview} showUrl={cardForm.show_url} />
        <label>Imagem do card<input type="file" accept="image/jpeg,image/png" required={!messageCard?.configured} onChange={(event) => selectCardImage(event.target.files?.[0])} /><small>JPG ou PNG, máximo de 5 MB.</small></label>
        <label>Texto do card<input value={cardForm.text} maxLength={120} required onChange={(event) => cardDraft.setValue({ ...cardForm, text: event.target.value })} /></label>
        <label>Link<input type="url" pattern="https://.*" maxLength={2048} value={cardForm.url} placeholder="https://..." required onChange={(event) => cardDraft.setValue({ ...cardForm, url: event.target.value })} /></label>
        <label className="checkbox-field"><input type="checkbox" checked={cardForm.show_url} onChange={(event) => cardDraft.setValue({ ...cardForm, show_url: event.target.checked })} /><span><strong>Exibir link no texto</strong><small>Desmarque para enviar um card interativo com o botão “Acessar”, sem mostrar o endereço.</small></span></label>
        <button className="button primary">Salvar card</button>
        </form>
        <form className="panel message-generation-settings" onSubmit={saveMessageGeneration}>
          <div className="step-heading"><span><Shuffle /></span><div><h2>Geração de variações</h2><p>Integração segura para criar versões leves do texto</p></div></div>
          <div className="generation-model"><span>Modelo fixo</span><strong>{messageGeneration?.model ?? "gpt-5.6-luna"}</strong></div>
          <label>API key da OpenAI<input type="password" value={openAIKey} autoComplete="new-password" placeholder={messageGeneration?.api_key_configured ? "Chave já cadastrada" : "sk-..."} onChange={(event) => setOpenAIKey(event.target.value)} /><small>A chave é armazenada criptografada e nunca retorna ao navegador. Deixe vazio para preservar a atual.</small></label>
          <div className={`generation-status ${messageGeneration?.api_key_configured ? "configured" : "missing"}`}><span />{messageGeneration?.api_key_configured ? "API key configurada" : "API key ainda não configurada"}</div>
          <button className="button primary">Salvar configuração</button>
        </form>
      </div>}
      {activeTab === "queries" && <div className="settings-grid">
        <form className="panel source-database-settings" onSubmit={saveDatabase}>
          <div className="step-heading"><span><Database /></span><div><h2>Banco da consulta</h2><p>Credenciais usadas somente para gerar a planilha</p></div></div>
          <div className="field-row"><label>SERVER_OLD<input value={databaseForm.server_old} required autoComplete="off" onChange={(event) => databaseDraft.setValue({ ...databaseForm, server_old: event.target.value })} /></label><label>DATABASE_OLD<input value={databaseForm.database_old} required autoComplete="off" onChange={(event) => databaseDraft.setValue({ ...databaseForm, database_old: event.target.value })} /></label></div>
          <div className="field-row"><label>USERNAME_OLD<input value={databaseForm.username_old} required autoComplete="username" onChange={(event) => databaseDraft.setValue({ ...databaseForm, username_old: event.target.value })} /></label><label>PASSWORD_OLD<input type="password" value={databasePassword} required={!sourceDatabase?.password_configured} autoComplete="new-password" placeholder={sourceDatabase?.password_configured ? "Senha já cadastrada" : "Informe a senha"} onChange={(event) => setDatabasePassword(event.target.value)} /></label></div>
          <small>A senha é armazenada criptografada e nunca é exibida novamente. Deixe o campo vazio para manter a senha atual.</small>
          <button className="button primary">Salvar credenciais</button>
        </form>
      </div>}
      {activeTab === "chips" && <div className="settings-grid">
        <form className="panel" onSubmit={save}><div className="step-heading"><span><Gauge /></span><div><h2>Cadência por chip</h2><p>Aplicada a novas campanhas</p></div></div><label>Teto diário por chip<input type="number" min="1" value={form.per_chip_daily_cap ?? ""} required onChange={(e) => runtimeDraft.setValue({ ...form, per_chip_daily_cap: Number(e.target.value) })} /></label><div className="field-row"><label>Início<input type="number" min="0" max="23" value={form.business_start_hour} onChange={(e) => runtimeDraft.setValue({ ...form, business_start_hour: Number(e.target.value) })} /></label><label>Fim<input type="number" min="1" max="24" value={form.business_end_hour} onChange={(e) => runtimeDraft.setValue({ ...form, business_end_hour: Number(e.target.value) })} /></label></div><label>Fuso horário<input value={form.timezone} onChange={(e) => runtimeDraft.setValue({ ...form, timezone: e.target.value })} /></label><button className="button primary">Salvar parâmetros</button></form>
        <form className="panel" onSubmit={syncFleet}><div className="step-heading"><span><Smartphone /></span><div><h2>Frota inicial</h2><p>{accountCount ? `${accountCount} chips configurados` : "Padrão inicial de 30 chips"}</p></div></div><p className="panel-copy">Defina a quantidade de estruturas lógicas da frota. Chips existentes dentro do intervalo são preservados; chips novos nascem inativos e precisam ser cadastrados e escaneados.</p><label>Quantidade de chips<input type="number" min="1" max="1000" step="1" value={fleetSize} required onChange={(event) => setFleetSize(Number(event.target.value))} /><small>Ao reduzir, as sessões e configurações dos chips que ficarem fora do intervalo serão excluídas.</small></label><button className="button secondary"><Plus />Ajustar frota para {fleetSize} chips</button></form>
      </div>}
      {activeTab === "users" && <div className="settings-grid">
        <form className="panel" onSubmit={createUser}><div className="step-heading"><span><Users /></span><div><h2>Novo usuário</h2><p>Perfis com auditoria individual</p></div></div><label>E-mail<input type="email" value={newUser.email} required onChange={(e) => newUserDraft.setValue({ ...newUser, email: e.target.value })} /></label><label>Senha<input type="password" minLength={10} value={newUserPassword} required onChange={(e) => setNewUserPassword(e.target.value)} /></label><label>Perfil<select value={newUser.role} onChange={(e) => newUserDraft.setValue({ ...newUser, role: e.target.value as User["role"] })}><option value="operator">Operador</option><option value="admin">Administrador</option></select></label><button className="button secondary">Criar usuário</button></form>
        <form className="panel" onSubmit={changePassword}><div className="step-heading"><span><ShieldCheck /></span><div><h2>Trocar minha senha</h2><p>Use pelo menos 12 caracteres</p></div></div><label>Senha atual<input type="password" value={passwords.current_password} required onChange={(e) => setPasswords({ ...passwords, current_password: e.target.value })} /></label><label>Nova senha<input type="password" minLength={12} value={passwords.new_password} required onChange={(e) => setPasswords({ ...passwords, new_password: e.target.value })} /></label><button className="button secondary">Alterar senha</button></form>
      </div>}
    </section>
  </div>;
}

export default function App() {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const [view, setView] = useState<View>("overview");
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  const [reviews, setReviews] = useState<ReviewItem[]>([]);
  const [settings, setSettings] = useState<RuntimeSettings | null>(null);
  const [sourceDatabase, setSourceDatabase] = useState<SourceDatabaseSettings | null>(null);
  const [messageCard, setMessageCard] = useState<MessageCardSettings | null>(null);
  const [messageGeneration, setMessageGeneration] = useState<MessageGenerationSettings | null>(null);
  const [qrAccount, setQrAccount] = useState<Account | null>(null);
  const userRef = useRef<User | null>(null);

  const clearDashboardState = useCallback(() => {
    setAccounts([]);
    setCampaigns([]);
    setReviews([]);
    setSettings(null);
    setSourceDatabase(null);
    setMessageCard(null);
    setMessageGeneration(null);
    setQrAccount(null);
    setView("overview");
  }, []);

  useEffect(() => { userRef.current = user; }, [user]);
  useEffect(() => setSessionExpiredHandler(() => {
    const currentUser = userRef.current;
    if (currentUser) clearUserSessionDrafts(currentUser.id);
    clearDashboardState();
    setUser(null);
  }), [clearDashboardState]);

  const loadOperationalData = useCallback(async () => {
    if (!user) return;
    const userId = user.id;
    const [nextAccounts, nextCampaigns, nextReviews] = await Promise.allSettled([
      api<Account[]>("/accounts"), api<Campaign[]>("/campaigns"), api<ReviewItem[]>("/reviews"),
    ]);
    if (userRef.current?.id !== userId) return;
    if (nextAccounts.status === "fulfilled") setAccounts(nextAccounts.value);
    if (nextCampaigns.status === "fulfilled") setCampaigns(nextCampaigns.value);
    if (nextReviews.status === "fulfilled") setReviews(nextReviews.value);
  }, [user]);

  const loadConfiguration = useCallback(async () => {
    if (!user) return;
    const userId = user.id;
    const [nextSettings, nextMessageCard, nextMessageGeneration, nextSourceDatabase] = await Promise.allSettled([
      api<RuntimeSettings>("/settings/runtime"),
      api<MessageCardSettings>("/settings/message-card"),
      api<MessageGenerationSettings>("/settings/message-generation"),
      user.role === "admin" ? api<SourceDatabaseSettings>("/settings/source-database") : Promise.resolve(null),
    ]);
    if (userRef.current?.id !== userId) return;
    if (nextSettings.status === "fulfilled") setSettings(nextSettings.value);
    if (nextMessageCard.status === "fulfilled") setMessageCard(nextMessageCard.value);
    if (nextMessageGeneration.status === "fulfilled") setMessageGeneration(nextMessageGeneration.value);
    if (nextSourceDatabase.status === "fulfilled") setSourceDatabase(nextSourceDatabase.value);
  }, [user]);

  const operationalCoordinator = useMemo(() => createRefreshCoordinator(loadOperationalData), [loadOperationalData]);
  const configurationCoordinator = useMemo(() => createRefreshCoordinator(loadConfiguration), [loadConfiguration]);
  const refreshOperational = useCallback(() => operationalCoordinator.run().catch(() => undefined), [operationalCoordinator]);
  const refreshConfiguration = useCallback(() => configurationCoordinator.run().catch(() => undefined), [configurationCoordinator]);

  useEffect(() => { api<User>("/auth/me").then(setUser).catch(() => setUser(null)).finally(() => setLoading(false)); }, []);
  useEffect(() => {
    if (!user) return;
    void refreshOperational();
    void refreshConfiguration();
  }, [user, refreshOperational, refreshConfiguration]);
  useEffect(() => {
    if (user && view === "settings") void refreshConfiguration();
  }, [user, view, refreshConfiguration]);
  useEffect(() => {
    if (!user) return;
    let disposed = false;
    let connected = false;
    let stream: EventSource | null = null;
    let reconnectTimer: number | null = null;
    let eventTimer: number | null = null;

    const scheduleReconnect = (delay: number) => {
      if (disposed || reconnectTimer !== null) return;
      reconnectTimer = window.setTimeout(() => {
        reconnectTimer = null;
        openStream();
      }, delay);
    };
    const recoverConnection = async () => {
      try {
        await api<User>("/auth/me");
        scheduleReconnect(1000);
      } catch {
        scheduleReconnect(5000);
      }
    };
    const openStream = () => {
      if (disposed || stream) return;
      const nextStream = new EventSource("/api/v1/events", { withCredentials: true });
      stream = nextStream;
      nextStream.onopen = () => { connected = true; };
      nextStream.onmessage = (event) => {
        if (!parseDashboardEvent(event.data)) return;
        if (eventTimer !== null) window.clearTimeout(eventTimer);
        eventTimer = window.setTimeout(() => { void refreshOperational(); }, 200);
      };
      nextStream.onerror = () => {
        connected = false;
        nextStream.close();
        if (stream === nextStream) stream = null;
        void recoverConnection();
      };
    };
    const fallbackTimer = window.setInterval(() => {
      if (!connected && document.visibilityState === "visible") void refreshOperational();
    }, 60000);
    const handleVisibility = () => {
      if (document.visibilityState !== "visible") return;
      void refreshOperational();
      if (!connected && !stream) scheduleReconnect(0);
    };
    document.addEventListener("visibilitychange", handleVisibility);
    openStream();
    return () => {
      disposed = true;
      stream?.close();
      window.clearInterval(fallbackTimer);
      if (reconnectTimer !== null) window.clearTimeout(reconnectTimer);
      if (eventTimer !== null) window.clearTimeout(eventTimer);
      document.removeEventListener("visibilitychange", handleVisibility);
    };
  }, [user, refreshOperational]);

  const ready = useMemo(() => accounts.filter((account) => account.state === "ready").length, [accounts]);
  const liveQrAccount = qrAccount ? accounts.find((account) => account.id === qrAccount.id) ?? qrAccount : null;
  async function logout() {
    const currentUser = userRef.current;
    try { await api("/auth/logout", { method: "POST" }); }
    finally {
      if (currentUser) clearUserSessionDrafts(currentUser.id);
      clearDashboardState();
      setUser(null);
    }
  }
  async function accountAction(account: Account, action: "connect" | "disconnect") {
    if (action === "disconnect" && !window.confirm(`Desconectar ${account.display_name}? A sessão salva será removida e um novo QR será exigido para reconectar.`)) return;
    try { await api(`/accounts/${account.id}/${action}`, { method: "POST" }); await refreshOperational(); }
    catch (cause) { window.alert(cause instanceof Error ? cause.message : "Falha ao atualizar o chip"); }
  }
  async function resetSession(account: Account) {
    if (!window.confirm(`Apagar a sessão de ${account.display_name} e gerar um novo QR?`)) return;
    try { await api(`/accounts/${account.id}/session`, { method: "DELETE" }); await refreshOperational(); }
    catch (cause) { window.alert(cause instanceof Error ? cause.message : "Falha ao refazer a sessão"); }
  }
  if (loading) return <div className="app-loading"><div className="brand-mark"><MessageSquareText /></div><span>Preparando central…</span></div>;
  if (!user) return <Login onLogin={setUser} />;

  const nav = [
    ["overview", "Visão geral", <LayoutDashboard />], ["campaign", "Campanhas", <MessageSquareText />], ["reviews", "Resultados", <AlertTriangle />], ["settings", "Configurações", <Settings />],
  ] as const;
  return <div className="app-shell">
    <aside className="sidebar"><div className="sidebar-brand"><div className="brand-mark"><MessageSquareText /></div><div><strong>AutoWpp</strong><span>Operations</span></div></div><nav>{nav.map(([id, label, icon]) => <button key={id} className={view === id ? "active" : ""} onClick={() => setView(id)}>{icon}<span>{label}</span>{id === "reviews" && reviews.length > 0 && <b>{reviews.length}</b>}</button>)}</nav><div className="sidebar-health"><span className={ready ? "online" : ""}><Activity /></span><div><strong>{ready} chips online</strong><small>{accounts.length} cadastrados</small></div></div><button className="profile" onClick={logout}><span>{user.email.slice(0, 2).toUpperCase()}</span><div><strong>{user.email}</strong><small>{user.role === "admin" ? "Administrador" : "Operador"}</small></div><LogOut /></button></aside>
    <main className="content">{view === "overview" && <Overview accounts={accounts} campaigns={campaigns} settings={settings} refresh={refreshOperational} onQr={setQrAccount} onConnect={(account) => accountAction(account, "connect")} onDisconnect={(account) => accountAction(account, "disconnect")} onReset={resetSession} />}{view === "campaign" && <CampaignBuilder userId={user.id} accounts={accounts} campaigns={campaigns} settings={settings} messageCard={messageCard} messageGeneration={messageGeneration} refresh={refreshOperational} />}{view === "reviews" && <Reviews items={reviews} refresh={refreshOperational} />}{view === "settings" && <SettingsView settings={settings} sourceDatabase={sourceDatabase} messageCard={messageCard} messageGeneration={messageGeneration} user={user} accountCount={accounts.length} refreshConfiguration={refreshConfiguration} refreshOperational={refreshOperational} />}</main>
    {liveQrAccount?.qr_code && <div className="modal-backdrop" onClick={() => setQrAccount(null)}><section className="qr-modal" onClick={(event) => event.stopPropagation()}><button className="modal-close" onClick={() => setQrAccount(null)}><X /></button><p className="eyebrow">AUTENTICAÇÃO</p><h2>{liveQrAccount.display_name}</h2><p>WhatsApp → Aparelhos conectados → Conectar aparelho</p><div className="qr-frame"><QRCodeSVG value={liveQrAccount.qr_code} size={260} level="M" /></div><span>O código é atualizado automaticamente.</span></section></div>}
  </div>;
}
