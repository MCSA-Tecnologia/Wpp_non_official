import { describe, expect, it, vi } from "vitest";
import { createRefreshCoordinator, parseDashboardEvent } from "../src/dashboard";

describe("dashboard events", () => {
  it("ignores heartbeats and malformed messages", () => {
    expect(parseDashboardEvent('{"type":"heartbeat"}')).toBeNull();
    expect(parseDashboardEvent("not-json")).toBeNull();
  });

  it("accepts real operational events", () => {
    expect(parseDashboardEvent('{"type":"account.status","account_id":"1"}')).toEqual({
      type: "account.status",
      account_id: "1",
    });
  });

  it("coalesces refresh bursts without overlapping requests", async () => {
    const releases: Array<() => void> = [];
    const task = vi.fn(() => new Promise<void>((resolve) => releases.push(resolve)));
    const coordinator = createRefreshCoordinator(task);

    const first = coordinator.run();
    const second = coordinator.run();
    const third = coordinator.run();
    expect(task).toHaveBeenCalledTimes(1);

    releases.shift()?.();
    await vi.waitFor(() => expect(task).toHaveBeenCalledTimes(2));
    releases.shift()?.();
    await Promise.all([first, second, third]);
    expect(task).toHaveBeenCalledTimes(2);
  });
});
