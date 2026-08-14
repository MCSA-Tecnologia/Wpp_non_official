import pino from "pino";
import { persistAckWithRetry } from "./ack-retry.js";
import type { ApiClient } from "./api-client.js";
import { config } from "./config.js";
import { BaileysConnector } from "./connectors/baileys.js";
import { prepareOutboundMessage, sendPreparedMessage } from "./outbound-message.js";
import type {
  AccountState,
  ClaimedAccount,
  ConnectorEvents,
  WhatsAppConnector,
} from "./types.js";

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

export class AccountAgent {
  private readonly log;
  private connector: WhatsAppConnector | null = null;
  private state: AccountState = "connecting";
  private error: string | undefined;
  private qrCode: string | undefined;
  private phone: string | undefined;
  private stopped = false;
  private reconnectAttempt = 0;

  constructor(
    private readonly api: ApiClient,
    readonly account: ClaimedAccount,
  ) {
    this.log = pino({ name: account.external_id, level: process.env.AUTOWPP_LOG_LEVEL ?? "info" });
  }

  start(): void {
    void this.connectionLoop();
    void this.heartbeatLoop();
    void this.jobLoop();
  }

  async stop(): Promise<void> {
    this.stopped = true;
    await this.connector?.close();
  }

  private events(): ConnectorEvents {
    return {
      onState: async (state, error, phone) => {
        this.state = state;
        this.error = error;
        this.phone = phone ?? this.phone;
        if (state === "ready") {
          this.qrCode = undefined;
          this.reconnectAttempt = 0;
        }
        await this.sendHeartbeat();
      },
      onQr: async (qr) => {
        this.state = "qr_required";
        this.qrCode = qr;
        await this.sendHeartbeat();
      },
      onAck: async (providerMessageId, ackLevel, payload = {}) => {
        try {
          await persistAckWithRetry(() => this.api.sendAck(providerMessageId, ackLevel, payload));
        } catch (error) {
          this.log.error({ error, providerMessageId, ackLevel }, "failed to persist ACK after retries");
        }
      },
    };
  }

  private createConnector(): WhatsAppConnector {
    return new BaileysConnector(this.api, this.account.id, this.events());
  }

  private async connectionLoop(): Promise<void> {
    while (!this.stopped) {
      try {
        this.connector = this.createConnector();
        this.state = "connecting";
        await this.sendHeartbeat();
        await this.connector.connect();
        while (!this.stopped && this.connector.isReady()) await sleep(1000);
      } catch (error) {
        this.error = error instanceof Error ? error.message : String(error);
        if (this.error === "logged_out") {
          this.state = "logged_out";
          await this.sendHeartbeat();
          return;
        }
        this.state = "backoff";
        this.reconnectAttempt += 1;
        await this.sendHeartbeat();
        const base = Math.min(300_000, 2_000 * 2 ** Math.min(this.reconnectAttempt, 7));
        await sleep(base * (0.7 + Math.random() * 0.6));
      } finally {
        await this.connector?.close().catch(() => undefined);
        this.connector = null;
      }
    }
  }

  private async sendHeartbeat(): Promise<void> {
    try {
      await this.api.heartbeat(this.account.id, this.state, {
        phone: this.phone,
        error: this.error,
        qr_code: this.qrCode,
      });
    } catch (error) {
      this.log.warn({ error }, "heartbeat failed");
    }
  }

  private async heartbeatLoop(): Promise<void> {
    while (!this.stopped) {
      await this.sendHeartbeat();
      await sleep(config.heartbeatMs);
    }
  }

  private async jobLoop(): Promise<void> {
    while (!this.stopped) {
      if (!this.connector?.isReady() || this.state !== "ready") {
        await sleep(config.jobPollMs);
        continue;
      }
      try {
        const job = await this.api.claimJob(this.account.id);
        if (!job) {
          await sleep(config.jobPollMs);
          continue;
        }
        const exists = await this.connector.validateRecipient(job.phone);
        if (!exists) {
          await this.api.updateJob(this.account.id, job, "failed", {
            error: "Número não registrado no WhatsApp",
          });
          continue;
        }
        const prepared = await prepareOutboundMessage(job, (assetId) =>
          this.api.getCardAsset(assetId),
        );
        if (prepared.imageError) {
          this.log.warn(
            {
              jobId: job.id,
              format: prepared.message.format,
              errorType: prepared.imageError instanceof Error
                ? prepared.imageError.name
                : "UnknownError",
            },
            "custom card image unavailable; using safe pre-send fallback",
          );
        }
        await this.api.updateJob(this.account.id, job, "sending");
        try {
          const providerId = await sendPreparedMessage(this.connector, job.phone, prepared.message);
          await this.api.updateJob(this.account.id, job, "sent", {
            provider_message_id: providerId,
          });
          this.log.info(
            { jobId: job.id, providerMessageId: providerId, format: prepared.message.format },
            "message sent",
          );
        } catch (error) {
          this.log.warn(
            {
              jobId: job.id,
              format: prepared.message.format,
              errorType: error instanceof Error ? error.name : "UnknownError",
            },
            "message send failed",
          );
          await this.api.updateJob(this.account.id, job, "failed", {
            error: error instanceof Error ? error.message : String(error),
          });
        }
      } catch (error) {
        this.log.warn({ error }, "job polling cycle failed");
        await sleep(2000);
      }
    }
  }
}
