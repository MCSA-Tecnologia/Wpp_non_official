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
  ShieldCheck,
  Smartphone,
  Upload,
  Users,
  X,
} from "lucide-react";
import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { QRCodeSVG } from "qrcode.react";
import { api, ApiError, downloadQueryExport } from "./api";
import type {
  Account,
  Campaign,
  Estimate,
  ImportBatch,
  MessageCardSettings,
  ReviewItem,
  RuntimeSettings,
  User,
} from "./types";

type View = "overview" | "campaign" | "reviews" | "settings";

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
        {!accounts.length && <div className="empty-state"><Smartphone /><strong>Nenhum chip cadastrado</strong><span>Um administrador pode criar os 30 chips em Configurações.</span></div>}
      </section>
    </div>
  );
}

function MessageCardPreview({ card, imageUrl }: { card: Pick<MessageCardSettings, "text" | "url">; imageUrl: string | null }) {
  let host = "Destino não configurado";
  try { if (card.url) host = new URL(card.url).hostname; } catch { /* The form/API displays validation. */ }
  return <div className="message-card-preview">
    {imageUrl ? <img src={imageUrl} alt="Imagem do card" /> : <div className="message-card-placeholder"><ImageIcon /></div>}
    <div><strong>{card.text || "Texto do card"}</strong><span>{host}</span></div>
  </div>;
}

function CampaignBuilder({ accounts, campaigns, settings, messageCard, refresh }: { accounts: Account[]; campaigns: Campaign[]; settings: RuntimeSettings | null; messageCard: MessageCardSettings | null; refresh: () => void }) {
  const [batch, setBatch] = useState<ImportBatch | null>(null);
  const [estimate, setEstimate] = useState<Estimate | null>(null);
  const [interval, setIntervalValue] = useState(0);
  const [name, setName] = useState("");
  const [message, setMessage] = useState("Bom dia NOME_DO_CLIENTE, temos uma informação importante para você.");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [reviewOpen, setReviewOpen] = useState(false);

  useEffect(() => {
    if (!batch || !settings?.per_chip_daily_cap) return;
    const timer = window.setTimeout(() => {
      api<Estimate>("/campaigns/estimate", { method: "POST", body: JSON.stringify({ import_id: batch.id, interval_mean_minutes: interval }) })
        .then((nextEstimate) => { setEstimate(nextEstimate); setError(""); })
        .catch((cause) => { setEstimate(null); setError(cause.message); });
    }, 250);
    return () => window.clearTimeout(timer);
  }, [batch, interval, settings?.per_chip_daily_cap, accounts]);

  async function upload(file: File) {
    setBusy(true); setError(""); setNotice("");
    const data = new FormData(); data.append("file", file);
    try {
      const result = await api<ImportBatch>("/imports/preview", { method: "POST", body: data });
      setBatch(result); setName(file.name.replace(/\.[^.]+$/, ""));
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Falha no upload"); }
    finally { setBusy(false); }
  }

  async function confirmAndStart() {
    if (!batch) return;
    setBusy(true); setError("");
    try {
      await api<Campaign>("/campaigns/confirm-and-start", { method: "POST", body: JSON.stringify({ name, import_id: batch.id, message, card_revision: messageCard?.revision ?? "", interval_mean_minutes: interval, confirmed_real_send: true }) });
      setReviewOpen(false); setNotice("Campanha real confirmada e agendada com sucesso."); setBatch(null); setEstimate(null); refresh();
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

  return <div className="view-stack">
    <section className="page-heading"><div><p className="eyebrow">CAMPANHAS</p><h1>Preparar novo disparo</h1><p>Valide os dados, ajuste a cadência e confira a previsão antes de iniciar.</p></div><button className="button secondary" onClick={exportQuery} disabled={busy}><Database size={16} />Gerar arquivo da query</button></section>
    {!settings?.per_chip_daily_cap && <div className="banner warning"><AlertTriangle /><div><strong>Teto diário por chip ainda não configurado</strong><span>Um administrador precisa definir o limite antes da primeira campanha.</span></div></div>}
    {!messageCard?.configured && <div className="banner warning"><AlertTriangle /><div><strong>Card da mensagem ainda não configurado</strong><span>Um administrador precisa definir imagem, texto e URL antes de iniciar uma campanha.</span></div></div>}
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
        <label>Nome da campanha<input value={name} onChange={(event) => setName(event.target.value)} placeholder="Ex.: Cobrança agosto" /></label>
        <label>Texto<textarea value={message} onChange={(event) => setMessage(event.target.value)} rows={5} /><small>Use NOME_DO_CLIENTE para inserir o primeiro e o último nomes e CREDOR para inserir o credor da planilha.</small></label>
      </article>
    </section>
    <section className="panel cadence-panel">
      <div className="step-heading"><span>03</span><div><h2>Cadência e previsão</h2><p>O intervalo é aplicado individualmente a cada chip.</p></div></div>
      <div className="slider-layout">
        <div className="slider-control"><div className="slider-value"><strong>{interval === 0 ? "Automático" : `${interval} min`}</strong><span>intervalo médio</span></div><input type="range" min="0" max="30" step="0.5" value={interval} onChange={(event) => setIntervalValue(Number(event.target.value))} /><div className="range-labels"><span>Auto</span><span>15 min</span><span>30 min</span></div></div>
        <div className="forecast-grid"><div><span>Contatos</span><strong>{estimate?.valid_contacts ?? batch?.valid_rows ?? "—"}</strong></div><div><span>Chips saudáveis</span><strong>{estimate?.healthy_accounts ?? accounts.filter((a) => a.state === "ready").length}</strong></div><div><span>Duração prevista</span><strong>{estimate ? formatDuration(estimate.duration_minutes) : "—"}</strong></div><div className="finish"><span>Conclusão estimada</span><strong>{formatDate(estimate?.estimated_finish_at)}</strong></div></div>
      </div>
      {estimate?.warnings.map((warning) => <div className="estimate-warning" key={warning}><AlertTriangle size={16} />{warning}</div>)}
      <div className="builder-actions"><span>Jitter de 70% a 130% · teto de {settings?.per_chip_daily_cap ?? "não definido"} por chip/dia</span><button className="button primary" disabled={!batch || !name || !message || !settings?.per_chip_daily_cap || !messageCard?.configured || !estimate?.healthy_accounts || busy} onClick={() => setReviewOpen(true)}><ShieldCheck size={17} />Revisar disparo</button></div>
    </section>
    {batch?.preview.length ? <section className="panel preview-panel"><div className="section-heading"><div><h2>Prévia da importação</h2><p>Primeiras {batch.preview.length} linhas</p></div></div><div className="table-wrap"><table><thead><tr><th>Linha</th><th>Nome</th><th>Telefone</th><th>Credor</th><th>Campanha</th><th>Status</th></tr></thead><tbody>{batch.preview.map((row) => <tr key={row.id}><td>{row.row_number}</td><td>{row.raw_data.Nome || row.raw_data.nome || "—"}</td><td>{row.normalized_phone || row.raw_data.Telefone}</td><td>{row.raw_data.Credor || "—"}</td><td>{row.raw_data.Campanha || "—"}</td><td><span className={`row-status ${row.valid ? "ok" : "error"}`}>{row.valid ? "Válida" : row.validation_error}</span></td></tr>)}</tbody></table></div></section> : null}
    <section className="panel"><div className="section-heading"><div><h2>Histórico de campanhas</h2><p>Últimas operações</p></div></div><div className="campaign-list">{campaigns.map((campaign) => <article key={campaign.id}><div><span className={`campaign-state ${campaign.state}`}>{campaignLabels[campaign.state] || campaign.state}</span><h3>{campaign.name}</h3><small>{campaign.sent}/{campaign.total} enviadas · {campaign.delivered} entregues · {campaign.failed} falharam · {campaign.review_required} sem confirmação</small></div><div className="campaign-progress"><span style={{ width: `${campaign.total ? (campaign.sent / campaign.total) * 100 : 0}%` }} /></div><div className="campaign-actions">{campaign.state === "draft" && <button onClick={() => campaignAction(campaign.id, "start")}><Play /></button>}{campaign.state === "active" && <button onClick={() => campaignAction(campaign.id, "pause")}><CirclePause /></button>}{campaign.state === "paused" && <button onClick={() => campaignAction(campaign.id, "resume")}><Play /></button>}{!["completed", "cancelled", "awaiting_results"].includes(campaign.state) && <button className="danger-icon" onClick={() => campaignAction(campaign.id, "cancel")}><X /></button>}</div></article>)}</div></section>
    {reviewOpen && batch && estimate && messageCard?.configured && <div className="modal-backdrop"><section className="confirm-modal"><button className="modal-close" onClick={() => setReviewOpen(false)}><X /></button><p className="eyebrow">ENVIO REAL</p><h2>Confirme antes de disparar</h2><div className="confirm-summary"><div><span>Arquivo</span><strong>{batch.filename}</strong></div><div><span>Linhas analisadas</span><strong>{batch.total_rows}</strong></div><div><span>Mensagens reais</span><strong>{batch.valid_rows}</strong></div><div><span>Duplicadas descartadas</span><strong>{batch.duplicate_rows}</strong></div><div><span>Inválidas descartadas</span><strong>{batch.invalid_rows}</strong></div><div><span>Chips prontos</span><strong>{estimate.healthy_accounts}</strong></div><div><span>Teto</span><strong>{estimate.per_chip_daily_cap} por chip/dia</strong></div><div><span>Capacidade restante hoje</span><strong>{estimate.remaining_capacity_today}</strong></div></div><div className="message-review"><span>Mensagem e card</span><p>{message}</p><MessageCardPreview card={messageCard} imageUrl={messageCard.image_url} /></div><div className="confirm-warning"><AlertTriangle />Esta ação enviará mensagens pelo WhatsApp real.</div><div className="confirm-actions"><button className="button secondary" onClick={() => setReviewOpen(false)}>Cancelar</button><button className="button primary" disabled={busy} onClick={confirmAndStart}><Play />Confirmar e iniciar {batch.valid_rows} mensagens reais</button></div></section></div>}
  </div>;
}

function Reviews({ items, refresh }: { items: ReviewItem[]; refresh: () => void }) {
  const [error, setError] = useState("");
  async function decide(id: string, action: string) { try { await api(`/reviews/${id}`, { method: "POST", body: JSON.stringify({ action }) }); refresh(); } catch (cause) { setError(cause instanceof Error ? cause.message : "Falha"); } }
  return <div className="view-stack"><section className="page-heading"><div><p className="eyebrow">RESULTADOS</p><h1>Envios que exigem atenção</h1><p>Falhas são reais; ausência de recibo é separada e nunca contada como entrega.</p></div></section>{error && <div className="banner danger"><AlertTriangle />{error}</div>}<section className="panel review-list">{items.length ? items.map((item) => <article key={item.id} className={item.state}><AlertTriangle /><div><strong>{item.phone} · {item.state === "failed" ? "Falha no envio" : "Entrega não confirmada"}</strong><span>{item.account || "Chip não identificado"} · iniciado {formatDate(item.started_at)}</span><small>{item.last_error}</small></div><div><button className="button secondary" onClick={() => decide(item.id, "retry")}>Autorizar retry</button><button className="button ghost danger-text" onClick={() => decide(item.id, "cancel")}>Cancelar</button></div></article>) : <div className="empty-state"><ShieldCheck /><strong>Nenhuma falha ou resultado pendente</strong><span>A fila está limpa.</span></div>}</section></div>;
}

function SettingsView({ settings, messageCard, user, refresh }: { settings: RuntimeSettings | null; messageCard: MessageCardSettings | null; user: User; refresh: () => void }) {
  const [form, setForm] = useState<RuntimeSettings>(settings ?? { per_chip_daily_cap: null, business_start_hour: 9, business_end_hour: 18, timezone: "America/Sao_Paulo" });
  const [notice, setNotice] = useState("");
  const [cardForm, setCardForm] = useState({ text: messageCard?.text ?? "", url: messageCard?.url ?? "" });
  const [cardImage, setCardImage] = useState<File | null>(null);
  const [cardPreview, setCardPreview] = useState<string | null>(messageCard?.image_url ?? null);
  const [newUser, setNewUser] = useState({ email: "", password: "", role: "operator" });
  const [passwords, setPasswords] = useState({ current_password: "", new_password: "" });
  useEffect(() => { if (settings) setForm(settings); }, [settings]);
  useEffect(() => {
    if (!messageCard) return;
    setCardForm({ text: messageCard.text, url: messageCard.url });
    setCardPreview(messageCard.image_url);
  }, [messageCard]);
  async function save(event: FormEvent) { event.preventDefault(); try { await api("/settings/runtime", { method: "PUT", body: JSON.stringify(form) }); setNotice("Configurações atualizadas."); refresh(); } catch (cause) { setNotice(cause instanceof Error ? cause.message : "Falha"); } }
  async function createAccounts() { try { await api("/accounts/bulk", { method: "POST", body: JSON.stringify({ count: 30, prefix: "chip" }) }); setNotice("Frota de 30 chips cadastrada como inativa."); refresh(); } catch (cause) { setNotice(cause instanceof Error ? cause.message : "Falha"); } }
  async function createUser(event: FormEvent) { event.preventDefault(); try { await api("/auth/users", { method: "POST", body: JSON.stringify(newUser) }); setNewUser({ email: "", password: "", role: "operator" }); setNotice("Usuário criado."); } catch (cause) { setNotice(cause instanceof Error ? cause.message : "Falha"); } }
  async function changePassword(event: FormEvent) { event.preventDefault(); try { await api("/auth/change-password", { method: "POST", body: JSON.stringify(passwords) }); setPasswords({ current_password: "", new_password: "" }); setNotice("Senha alterada."); } catch (cause) { setNotice(cause instanceof Error ? cause.message : "Falha"); } }
  async function saveCard(event: FormEvent) {
    event.preventDefault();
    const data = new FormData();
    data.append("text", cardForm.text);
    data.append("url", cardForm.url);
    if (cardImage) data.append("image", cardImage);
    try {
      await api<MessageCardSettings>("/settings/message-card", { method: "PUT", body: data });
      setCardImage(null); setNotice("Card da mensagem atualizado."); await refresh();
    } catch (cause) { setNotice(cause instanceof Error ? cause.message : "Falha ao salvar o card"); }
  }
  function selectCardImage(file: File | undefined) {
    if (!file) return;
    setCardImage(file);
    setCardPreview(URL.createObjectURL(file));
  }
  if (user.role !== "admin") return <div className="empty-state full-page"><ShieldCheck /><strong>Acesso restrito</strong><span>Somente administradores alteram parâmetros operacionais.</span></div>;
  return <div className="view-stack">
    <section className="page-heading"><div><p className="eyebrow">ADMINISTRAÇÃO</p><h1>Parâmetros de operação</h1><p>Limites, janela comercial, frota e acesso.</p></div></section>
    {notice && <div className="banner neutral"><Check />{notice}</div>}
    <section className="settings-grid">
      <form className="panel message-card-settings" onSubmit={saveCard}>
        <div className="step-heading"><span><ImageIcon /></span><div><h2>Card da mensagem</h2><p>Aplicado a todas as novas campanhas</p></div></div>
        <MessageCardPreview card={cardForm} imageUrl={cardPreview} />
        <label>Imagem JPG ou PNG<input type="file" accept="image/jpeg,image/png" required={!messageCard?.configured} onChange={(event) => selectCardImage(event.target.files?.[0])} /><small>Máximo de 5 MB. A imagem será otimizada automaticamente.</small></label>
        <label>Texto do card<input value={cardForm.text} maxLength={120} required onChange={(event) => setCardForm({ ...cardForm, text: event.target.value })} /></label>
        <label>URL de destino<input type="url" pattern="https://.*" maxLength={2048} value={cardForm.url} placeholder="https://..." required onChange={(event) => setCardForm({ ...cardForm, url: event.target.value })} /></label>
        <button className="button primary">Salvar card</button>
      </form>
      <form className="panel" onSubmit={save}><div className="step-heading"><span><Gauge /></span><div><h2>Cadência por chip</h2><p>Aplicada a novas campanhas</p></div></div><label>Teto diário por chip<input type="number" min="1" value={form.per_chip_daily_cap ?? ""} required onChange={(e) => setForm({ ...form, per_chip_daily_cap: Number(e.target.value) })} /></label><div className="field-row"><label>Início<input type="number" min="0" max="23" value={form.business_start_hour} onChange={(e) => setForm({ ...form, business_start_hour: Number(e.target.value) })} /></label><label>Fim<input type="number" min="1" max="24" value={form.business_end_hour} onChange={(e) => setForm({ ...form, business_end_hour: Number(e.target.value) })} /></label></div><label>Fuso horário<input value={form.timezone} onChange={(e) => setForm({ ...form, timezone: e.target.value })} /></label><button className="button primary">Salvar parâmetros</button></form>
      <article className="panel"><div className="step-heading"><span><Smartphone /></span><div><h2>Frota inicial</h2><p>Criação idempotente de chip_01 a chip_30</p></div></div><p className="panel-copy">Cadastre a estrutura lógica dos 30 chips. Todos nascem inativos e cada um deve ser ativado e autenticado por QR individualmente.</p><button className="button secondary" onClick={createAccounts}><Plus />Criar ou completar 30 chips</button></article>
      <form className="panel" onSubmit={createUser}><div className="step-heading"><span><Users /></span><div><h2>Novo usuário</h2><p>Perfis com auditoria individual</p></div></div><label>E-mail<input type="email" value={newUser.email} required onChange={(e) => setNewUser({ ...newUser, email: e.target.value })} /></label><label>Senha<input type="password" minLength={10} value={newUser.password} required onChange={(e) => setNewUser({ ...newUser, password: e.target.value })} /></label><label>Perfil<select value={newUser.role} onChange={(e) => setNewUser({ ...newUser, role: e.target.value })}><option value="operator">Operador</option><option value="admin">Administrador</option></select></label><button className="button secondary">Criar usuário</button></form>
      <form className="panel" onSubmit={changePassword}><div className="step-heading"><span><ShieldCheck /></span><div><h2>Trocar minha senha</h2><p>Use pelo menos 12 caracteres</p></div></div><label>Senha atual<input type="password" value={passwords.current_password} required onChange={(e) => setPasswords({ ...passwords, current_password: e.target.value })} /></label><label>Nova senha<input type="password" minLength={12} value={passwords.new_password} required onChange={(e) => setPasswords({ ...passwords, new_password: e.target.value })} /></label><button className="button secondary">Alterar senha</button></form>
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
  const [messageCard, setMessageCard] = useState<MessageCardSettings | null>(null);
  const [qrAccount, setQrAccount] = useState<Account | null>(null);

  const loadData = useCallback(async () => {
    if (!user) return;
    try {
      const [nextAccounts, nextCampaigns, nextReviews, nextSettings, nextMessageCard] = await Promise.all([
        api<Account[]>("/accounts"), api<Campaign[]>("/campaigns"), api<ReviewItem[]>("/reviews"), api<RuntimeSettings>("/settings/runtime"), api<MessageCardSettings>("/settings/message-card"),
      ]);
      setAccounts(nextAccounts); setCampaigns(nextCampaigns); setReviews(nextReviews); setSettings(nextSettings); setMessageCard(nextMessageCard);
    } catch (cause) { if (cause instanceof ApiError && cause.status === 401) setUser(null); }
  }, [user]);

  useEffect(() => { api<User>("/auth/me").then(setUser).catch(() => setUser(null)).finally(() => setLoading(false)); }, []);
  useEffect(() => { if (!user) return; void loadData(); const timer = window.setInterval(loadData, 15000); const stream = new EventSource("/api/v1/events", { withCredentials: true }); stream.onmessage = () => void loadData(); return () => { window.clearInterval(timer); stream.close(); }; }, [user, loadData]);

  const ready = useMemo(() => accounts.filter((account) => account.state === "ready").length, [accounts]);
  const liveQrAccount = qrAccount ? accounts.find((account) => account.id === qrAccount.id) ?? qrAccount : null;
  async function logout() { await api("/auth/logout", { method: "POST" }); setUser(null); }
  async function accountAction(account: Account, action: "connect" | "disconnect") {
    if (action === "disconnect" && !window.confirm(`Desconectar ${account.display_name}? A sessão salva será removida e um novo QR será exigido para reconectar.`)) return;
    try { await api(`/accounts/${account.id}/${action}`, { method: "POST" }); await loadData(); }
    catch (cause) { window.alert(cause instanceof Error ? cause.message : "Falha ao atualizar o chip"); }
  }
  async function resetSession(account: Account) {
    if (!window.confirm(`Apagar a sessão de ${account.display_name} e gerar um novo QR?`)) return;
    try { await api(`/accounts/${account.id}/session`, { method: "DELETE" }); await loadData(); }
    catch (cause) { window.alert(cause instanceof Error ? cause.message : "Falha ao refazer a sessão"); }
  }
  if (loading) return <div className="app-loading"><div className="brand-mark"><MessageSquareText /></div><span>Preparando central…</span></div>;
  if (!user) return <Login onLogin={setUser} />;

  const nav = [
    ["overview", "Visão geral", <LayoutDashboard />], ["campaign", "Campanhas", <MessageSquareText />], ["reviews", "Resultados", <AlertTriangle />], ["settings", "Configurações", <Settings />],
  ] as const;
  return <div className="app-shell">
    <aside className="sidebar"><div className="sidebar-brand"><div className="brand-mark"><MessageSquareText /></div><div><strong>AutoWpp</strong><span>Operations</span></div></div><nav>{nav.map(([id, label, icon]) => <button key={id} className={view === id ? "active" : ""} onClick={() => setView(id)}>{icon}<span>{label}</span>{id === "reviews" && reviews.length > 0 && <b>{reviews.length}</b>}</button>)}</nav><div className="sidebar-health"><span className={ready ? "online" : ""}><Activity /></span><div><strong>{ready} chips online</strong><small>{accounts.length} cadastrados</small></div></div><button className="profile" onClick={logout}><span>{user.email.slice(0, 2).toUpperCase()}</span><div><strong>{user.email}</strong><small>{user.role === "admin" ? "Administrador" : "Operador"}</small></div><LogOut /></button></aside>
    <main className="content">{view === "overview" && <Overview accounts={accounts} campaigns={campaigns} settings={settings} refresh={loadData} onQr={setQrAccount} onConnect={(account) => accountAction(account, "connect")} onDisconnect={(account) => accountAction(account, "disconnect")} onReset={resetSession} />}{view === "campaign" && <CampaignBuilder accounts={accounts} campaigns={campaigns} settings={settings} messageCard={messageCard} refresh={loadData} />}{view === "reviews" && <Reviews items={reviews} refresh={loadData} />}{view === "settings" && <SettingsView settings={settings} messageCard={messageCard} user={user} refresh={loadData} />}</main>
    {liveQrAccount?.qr_code && <div className="modal-backdrop" onClick={() => setQrAccount(null)}><section className="qr-modal" onClick={(event) => event.stopPropagation()}><button className="modal-close" onClick={() => setQrAccount(null)}><X /></button><p className="eyebrow">AUTENTICAÇÃO</p><h2>{liveQrAccount.display_name}</h2><p>WhatsApp → Aparelhos conectados → Conectar aparelho</p><div className="qr-frame"><QRCodeSVG value={liveQrAccount.qr_code} size={260} level="M" /></div><span>O código é atualizado automaticamente.</span></section></div>}
  </div>;
}
