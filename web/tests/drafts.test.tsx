import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";
import {
  clearUserSessionDrafts,
  readSessionDraft,
  useSessionDraft,
  writeSessionDraft,
} from "../src/drafts";
import type { SourceDatabaseDraft } from "../src/types";

beforeEach(() => window.sessionStorage.clear());

describe("session drafts", () => {
  it("survives component navigation and does not accept server hydration while dirty", () => {
    const initial = { version: 1 as const, name: "server" };
    const { result, unmount } = renderHook(() => useSessionDraft("user-1", "campaign", initial));

    act(() => result.current.setValue({ version: 1, name: "unsaved" }));
    act(() => result.current.hydrate({ version: 1, name: "new server value" }));
    expect(result.current.value.name).toBe("unsaved");
    unmount();

    const restored = renderHook(() => useSessionDraft("user-1", "campaign", initial));
    expect(restored.result.current.value.name).toBe("unsaved");
  });

  it("discards corrupt and obsolete payloads", () => {
    window.sessionStorage.setItem("autowpp:draft:v1:user-1:campaign", "bad-json");
    expect(readSessionDraft("user-1", "campaign")).toBeNull();
    window.sessionStorage.setItem("autowpp:draft:v1:user-1:campaign", '{"version":0}');
    expect(readSessionDraft("user-1", "campaign")).toBeNull();
  });

  it("clears only the selected user's drafts on logout", () => {
    writeSessionDraft("user-1", "campaign", { version: 1, name: "draft" });
    writeSessionDraft("user-2", "campaign", { version: 1, name: "other" });
    clearUserSessionDrafts("user-1");
    expect(readSessionDraft("user-1", "campaign")).toBeNull();
    expect(readSessionDraft("user-2", "campaign")).not.toBeNull();
  });

  it("stores database coordinates without secret fields", () => {
    const safeDraft: SourceDatabaseDraft = {
      version: 1,
      server_old: "db.example",
      database_old: "contacts",
      username_old: "reader",
    };
    writeSessionDraft("user-1", "settings-database", safeDraft);
    const stored = window.sessionStorage.getItem("autowpp:draft:v1:user-1:settings-database") ?? "";
    expect(stored).not.toContain("password");
  });
});
