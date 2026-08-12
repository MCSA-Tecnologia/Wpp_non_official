import pino from "pino";
import { AccountAgent } from "./account-agent.js";
import type { ApiClient } from "./api-client.js";
import { config } from "./config.js";

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));
const log = pino({ name: "supervisor", level: process.env.AUTOWPP_LOG_LEVEL ?? "info" });

export class Supervisor {
  private agents = new Map<string, AccountAgent>();
  private stopped = false;
  private reconciling = false;

  constructor(private readonly api: ApiClient) {}

  async run(): Promise<void> {
    log.info(
      { workerId: config.workerId, nodeId: config.nodeId, capacity: config.capacity },
      "worker started",
    );
    void this.commandLoop();
    while (!this.stopped) {
      await this.reconcile();
      await sleep(config.claimAccountsMs);
    }
  }

  private async reconcile(): Promise<void> {
    if (this.reconciling) return;
    this.reconciling = true;
    try {
      try {
        const claimed = await this.api.claimAccounts();
        const activeIds = new Set(claimed.map((account) => account.id));
        for (const account of claimed) {
          const existing = this.agents.get(account.id);
          if (existing && existing.account.session_revision !== account.session_revision) {
            await existing.stop();
            this.agents.delete(account.id);
          }
          if (!this.agents.has(account.id)) {
            const agent = new AccountAgent(this.api, account);
            this.agents.set(account.id, agent);
            agent.start();
          }
        }
        for (const [id, agent] of this.agents) {
          if (!activeIds.has(id)) {
            await agent.stop();
            this.agents.delete(id);
          }
        }
      } catch (error) {
        log.error({ error }, "unable to claim accounts");
      }
    } finally {
      this.reconciling = false;
    }
  }

  private async commandLoop(): Promise<void> {
    while (!this.stopped) {
      try {
        await this.api.watchCommands(async () => this.reconcile());
      } catch (error) {
        log.warn({ error }, "command stream unavailable; polling remains active");
        await sleep(2000);
      }
    }
  }

  async stop(): Promise<void> {
    this.stopped = true;
    await Promise.all([...this.agents.values()].map((agent) => agent.stop()));
    this.agents.clear();
  }
}
