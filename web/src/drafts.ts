import { Dispatch, SetStateAction, useCallback, useState } from "react";

const DRAFT_PREFIX = "autowpp:draft:v1";

function storageKey(userId: string, scope: string) {
  return `${DRAFT_PREFIX}:${userId}:${scope}`;
}

export function readSessionDraft<T>(userId: string, scope: string): T | null {
  try {
    const raw = window.sessionStorage.getItem(storageKey(userId, scope));
    if (!raw) return null;
    const value = JSON.parse(raw) as T & { version?: unknown };
    if (!value || value.version !== 1) {
      window.sessionStorage.removeItem(storageKey(userId, scope));
      return null;
    }
    return value;
  } catch {
    try { window.sessionStorage.removeItem(storageKey(userId, scope)); } catch { /* Storage is unavailable. */ }
    return null;
  }
}

export function writeSessionDraft<T>(userId: string, scope: string, value: T): void {
  try { window.sessionStorage.setItem(storageKey(userId, scope), JSON.stringify(value)); }
  catch { /* The form still works when storage is unavailable or full. */ }
}

export function removeSessionDraft(userId: string, scope: string): void {
  try { window.sessionStorage.removeItem(storageKey(userId, scope)); }
  catch { /* Storage is unavailable. */ }
}

export function clearUserSessionDrafts(userId: string): void {
  try {
    const prefix = `${DRAFT_PREFIX}:${userId}:`;
    const keys: string[] = [];
    for (let index = 0; index < window.sessionStorage.length; index += 1) {
      const key = window.sessionStorage.key(index);
      if (key?.startsWith(prefix)) keys.push(key);
    }
    keys.forEach((key) => window.sessionStorage.removeItem(key));
  } catch { /* Storage is unavailable. */ }
}

interface SessionDraftState<T> {
  value: T;
  dirty: boolean;
  setValue: Dispatch<SetStateAction<T>>;
  hydrate: (value: T) => void;
  clear: (value: T) => void;
}

export function useSessionDraft<T>(userId: string, scope: string, fallback: T): SessionDraftState<T> {
  const [stored] = useState(() => readSessionDraft<T>(userId, scope));
  const [value, setStoredValue] = useState<T>(() => stored ?? fallback);
  const [dirty, setDirty] = useState(() => stored !== null);

  const setValue = useCallback<Dispatch<SetStateAction<T>>>((next) => {
    setStoredValue((current) => {
      const resolved = typeof next === "function"
        ? (next as (previous: T) => T)(current)
        : next;
      writeSessionDraft(userId, scope, resolved);
      return resolved;
    });
    setDirty(true);
  }, [scope, userId]);

  const hydrate = useCallback((next: T) => {
    if (!dirty) setStoredValue(next);
  }, [dirty]);

  const clear = useCallback((next: T) => {
    removeSessionDraft(userId, scope);
    setStoredValue(next);
    setDirty(false);
  }, [scope, userId]);

  return { value, dirty, setValue, hydrate, clear };
}
