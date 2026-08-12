import makeWASocket, {
  Browsers,
  DisconnectReason,
  fetchLatestBaileysVersion,
  makeCacheableSignalKeyStore,
} from "@whiskeysockets/baileys";
import { Boom } from "@hapi/boom";
import pino from "pino";
import type { ApiClient } from "../api-client.js";
import { loadRemoteAuthState } from "../remote-auth.js";
import type { ConnectorEvents, MessageCard, WhatsAppConnector } from "../types.js";
import type { AnyMessageContent } from "@whiskeysockets/baileys";

const logger = pino({ level: process.env.AUTOWPP_LOG_LEVEL ?? "info" });

export function normalizeMessageStatus(status: unknown): number | null {
  if (status === undefined || status === null) return null;
  const numeric = Number(status);
  if (!Number.isFinite(numeric) || numeric < 0) return null;
  return Math.min(numeric, 4);
}

export function buildCardMessage(message: string, card: MessageCard): AnyMessageContent {
  return {
    text: message,
    contextInfo: {
      externalAdReply: {
        title: card.text,
        body: new URL(card.url).hostname,
        mediaType: 1,
        thumbnail: card.image,
        sourceUrl: card.url,
        renderLargerThumbnail: true,
        showAdAttribution: false,
      },
    },
  };
}

export class BaileysConnector implements WhatsAppConnector {
  private socket: ReturnType<typeof makeWASocket> | null = null;
  private ready = false;

  constructor(
    private readonly api: ApiClient,
    private readonly accountId: string,
    private readonly events: ConnectorEvents,
  ) {}

  async connect(): Promise<void> {
    const { state, saveCreds } = await loadRemoteAuthState(this.api, this.accountId);
    const { version } = await fetchLatestBaileysVersion();
    const socket = makeWASocket({
      version,
      auth: {
        creds: state.creds,
        keys: makeCacheableSignalKeyStore(state.keys, logger),
      },
      browser: Browsers.ubuntu("AutoWpp"),
      logger,
      printQRInTerminal: false,
      markOnlineOnConnect: false,
      syncFullHistory: false,
      connectTimeoutMs: 60_000,
      keepAliveIntervalMs: 20_000,
    });
    this.socket = socket;
    socket.ev.on("creds.update", saveCreds);
    socket.ev.on("messages.update", (updates) => {
      for (const item of updates) {
        const providerId = item.key.id;
        const status = normalizeMessageStatus(item.update.status);
        if (providerId && status !== null) void this.events.onAck(providerId, status, item.update);
      }
    });
    socket.ev.on("message-receipt.update", (updates) => {
      for (const item of updates) {
        const providerId = item.key.id;
        const ackLevel = item.receipt.playedTimestamp || item.receipt.readTimestamp ? 4 : 3;
        if (providerId) {
          void this.events.onAck(providerId, ackLevel, {
            source: "message-receipt.update",
            receipt: item.receipt,
          });
        }
      }
    });

    await new Promise<void>((resolve, reject) => {
      socket.ev.on("connection.update", async (update) => {
        if (update.qr) void this.events.onQr(update.qr);
        if (update.connection === "open") {
          try {
            await saveCreds();
            const digits = socket.user?.id.split(":")[0]?.replace(/\D/g, "");
            if (!digits) throw new Error("WhatsApp conectado sem identificar o número da conta");
            this.ready = true;
            await this.events.onState("ready", undefined, `+${digits}`);
            resolve();
          } catch (error) {
            this.ready = false;
            reject(error);
          }
        }
        if (update.connection === "close") {
          this.ready = false;
          const statusCode = (update.lastDisconnect?.error as Boom | undefined)?.output?.statusCode;
          if (statusCode === DisconnectReason.loggedOut) {
            void this.events.onState("logged_out", "Sessão desconectada pelo WhatsApp");
            reject(new Error("logged_out"));
          } else {
            const reason = String(update.lastDisconnect?.error ?? "connection_closed");
            void this.events.onState("backoff", reason);
            reject(new Error(reason));
          }
        }
      });
    });
  }

  isReady(): boolean {
    return this.ready && Boolean(this.socket);
  }

  async validateRecipient(phone: string): Promise<boolean> {
    if (!this.socket) return false;
    const jid = `${phone.replace(/\D/g, "")}@s.whatsapp.net`;
    const result = await this.socket.onWhatsApp(jid);
    return Boolean(result?.[0]?.exists);
  }

  async send(phone: string, message: string, card: MessageCard): Promise<string> {
    if (!this.socket || !this.ready) throw new Error("WhatsApp não está conectado");
    const jid = `${phone.replace(/\D/g, "")}@s.whatsapp.net`;
    const sent = await this.socket.sendMessage(jid, buildCardMessage(message, card));
    if (!sent?.key.id) throw new Error("WhatsApp não retornou o identificador da mensagem");
    return sent.key.id;
  }

  async close(): Promise<void> {
    this.ready = false;
    if (this.socket) {
      this.socket.ev.removeAllListeners("connection.update");
      this.socket.ev.removeAllListeners("messages.update");
      this.socket.ev.removeAllListeners("message-receipt.update");
      this.socket.end(undefined);
      this.socket = null;
    }
  }
}
