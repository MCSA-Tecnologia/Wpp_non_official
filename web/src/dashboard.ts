import type { DashboardEvent } from "./types";

export function parseDashboardEvent(data: string): DashboardEvent | null {
  try {
    const event = JSON.parse(data) as DashboardEvent;
    if (!event || typeof event.type !== "string" || event.type === "heartbeat") return null;
    return event;
  } catch {
    return null;
  }
}

export interface RefreshCoordinator {
  run: () => Promise<void>;
}

export function createRefreshCoordinator(task: () => Promise<void>): RefreshCoordinator {
  let running: Promise<void> | null = null;
  let queued = false;

  return {
    run() {
      if (running) {
        queued = true;
        return running;
      }
      running = (async () => {
        do {
          queued = false;
          await task();
        } while (queued);
      })().finally(() => { running = null; });
      return running;
    },
  };
}
