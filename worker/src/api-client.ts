import { config } from "./config.js";
import type { AccountState, AuthRecord, ClaimedAccount, ClaimedJob } from "./types.js";

export class ApiClient {
  private readonly assetCache = new Map<string, Promise<Uint8Array>>();

  private async request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const response = await fetch(`${config.apiUrl}${path}`, {
      ...init,
      headers: {
        "Content-Type": "application/json",
        "X-Worker-Token": config.workerToken,
        ...(init.headers ?? {}),
      },
    });
    if (!response.ok) {
      const detail = await response.text();
      throw new Error(`API ${response.status}: ${detail}`);
    }
    if (response.status === 204) return undefined as T;
    return (await response.json()) as T;
  }

  claimAccounts(): Promise<ClaimedAccount[]> {
    return this.request("/internal/workers/claim-accounts", {
      method: "POST",
      body: JSON.stringify({
        worker_id: config.workerId,
        node_id: config.nodeId,
        capacity: config.capacity,
      }),
    });
  }

  heartbeat(
    accountId: string,
    state: AccountState,
    details: { phone?: string; error?: string; qr_code?: string } = {},
  ): Promise<{ lease_until: string }> {
    return this.request(`/internal/accounts/${accountId}/heartbeat`, {
      method: "POST",
      body: JSON.stringify({
        worker_id: config.workerId,
        node_id: config.nodeId,
        state,
        ...details,
      }),
    });
  }

  getAuth(accountId: string): Promise<AuthRecord[]> {
    return this.request(
      `/internal/accounts/${accountId}/auth?worker_id=${encodeURIComponent(config.workerId)}`,
    );
  }

  saveAuth(accountId: string, records: AuthRecord[]): Promise<void> {
    return this.request(
      `/internal/accounts/${accountId}/auth?worker_id=${encodeURIComponent(config.workerId)}`,
      { method: "PUT", body: JSON.stringify({ records }) },
    );
  }

  claimJob(accountId: string): Promise<ClaimedJob | null> {
    return this.request(
      `/internal/accounts/${accountId}/jobs/claim?worker_id=${encodeURIComponent(config.workerId)}`,
      { method: "POST" },
    );
  }

  getCardAsset(assetId: string): Promise<Uint8Array> {
    const cached = this.assetCache.get(assetId);
    if (cached) return cached;
    const pending = fetch(`${config.apiUrl}/internal/assets/${encodeURIComponent(assetId)}`, {
      headers: { "X-Worker-Token": config.workerToken },
    }).then(async (response) => {
      if (!response.ok) {
        throw new Error(`API ${response.status}: ${await response.text()}`);
      }
      return new Uint8Array(await response.arrayBuffer());
    }).catch((error) => {
      this.assetCache.delete(assetId);
      throw error;
    });
    this.assetCache.set(assetId, pending);
    return pending;
  }

  updateJob(
    accountId: string,
    job: ClaimedJob,
    state: "sending" | "sent" | "failed",
    details: { provider_message_id?: string; error?: string } = {},
  ): Promise<void> {
    return this.request(
      `/internal/accounts/${accountId}/jobs/${job.id}/result?worker_id=${encodeURIComponent(config.workerId)}`,
      {
        method: "POST",
        body: JSON.stringify({ lease_token: job.lease_token, state, ...details }),
      },
    );
  }

  sendAck(providerMessageId: string, ackLevel: number, payload: object = {}): Promise<void> {
    return this.request("/internal/events/ack", {
      method: "POST",
      body: JSON.stringify({
        provider_message_id: providerMessageId,
        ack_level: ackLevel,
        payload,
      }),
    });
  }

  async watchCommands(onCommand: (payload: unknown) => Promise<void>): Promise<void> {
    const response = await fetch(`${config.apiUrl}/internal/events`, {
      headers: { "X-Worker-Token": config.workerToken },
    });
    if (!response.ok || !response.body) throw new Error(`command stream failed: ${response.status}`);
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const { value, done } = await reader.read();
      if (done) throw new Error("command stream closed");
      buffer += decoder.decode(value, { stream: true });
      let boundary = buffer.indexOf("\n\n");
      while (boundary >= 0) {
        const frame = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        const data = frame.split("\n").find((line) => line.startsWith("data: "))?.slice(6);
        if (data) await onCommand(JSON.parse(data));
        boundary = buffer.indexOf("\n\n");
      }
    }
  }
}
