import { afterEach, describe, expect, it, vi } from "vitest";
import { api, setSessionExpiredHandler } from "../src/api";

function jsonResponse(status: number, body: unknown = {}) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

afterEach(() => {
  setSessionExpiredHandler(null);
  vi.unstubAllGlobals();
});

describe("authenticated API requests", () => {
  it("refreshes an expired access token and retries once", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse(401, { detail: "expired" }))
      .mockResolvedValueOnce(jsonResponse(200, { id: "user" }))
      .mockResolvedValueOnce(jsonResponse(200, { ok: true }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(api<{ ok: boolean }>("/accounts")).resolves.toEqual({ ok: true });
    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
      "/api/v1/accounts",
      "/api/v1/auth/refresh",
      "/api/v1/accounts",
    ]);
  });

  it("deduplicates refreshes from concurrent 401 responses", async () => {
    let regularRequests = 0;
    const fetchMock = vi.fn(async (url: string) => {
      if (url === "/api/v1/auth/refresh") return jsonResponse(200, { id: "user" });
      regularRequests += 1;
      return regularRequests <= 2 ? jsonResponse(401) : jsonResponse(200, { ok: true });
    });
    vi.stubGlobal("fetch", fetchMock);

    await Promise.all([api("/accounts"), api("/campaigns")]);
    expect(fetchMock.mock.calls.filter(([url]) => url === "/api/v1/auth/refresh")).toHaveLength(1);
  });

  it("expires the UI session only when refresh is definitively unauthorized", async () => {
    const expired = vi.fn();
    setSessionExpiredHandler(expired);
    vi.stubGlobal("fetch", vi.fn()
      .mockResolvedValueOnce(jsonResponse(401))
      .mockResolvedValueOnce(jsonResponse(401)));

    await expect(api("/accounts")).rejects.toMatchObject({ status: 401 });
    expect(expired).toHaveBeenCalledTimes(1);
  });

  it("does not expire the UI session for a refresh network failure", async () => {
    const expired = vi.fn();
    setSessionExpiredHandler(expired);
    vi.stubGlobal("fetch", vi.fn()
      .mockResolvedValueOnce(jsonResponse(401))
      .mockRejectedValueOnce(new TypeError("offline")));

    await expect(api("/accounts")).rejects.toThrow("offline");
    expect(expired).not.toHaveBeenCalled();
  });

  it("does not expire the UI session for an ordinary server failure", async () => {
    const expired = vi.fn();
    setSessionExpiredHandler(expired);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(jsonResponse(500, { detail: "temporary" })));

    await expect(api("/accounts")).rejects.toMatchObject({ status: 500 });
    expect(expired).not.toHaveBeenCalled();
  });
});
