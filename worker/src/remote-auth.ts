import {
  BufferJSON,
  initAuthCreds,
  proto,
  type AuthenticationCreds,
  type AuthenticationState,
  type SignalDataTypeMap,
} from "@whiskeysockets/baileys";
import type { ApiClient } from "./api-client.js";
import type { AuthRecord } from "./types.js";

const serialize = (value: unknown): unknown =>
  JSON.parse(JSON.stringify(value, BufferJSON.replacer));

const deserialize = <T>(value: unknown): T =>
  JSON.parse(JSON.stringify(value), BufferJSON.reviver) as T;

export async function loadRemoteAuthState(api: ApiClient, accountId: string): Promise<{
  state: AuthenticationState;
  saveCreds: () => Promise<void>;
}> {
  const records = await api.getAuth(accountId);
  const credsRecord = records.find(
    (record) => record.category === "creds" && record.key_id === "default",
  );
  const creds: AuthenticationCreds = credsRecord
    ? deserialize<AuthenticationCreds>(credsRecord.value)
    : initAuthCreds();
  const keyCache = new Map<string, unknown>();
  for (const record of records.filter((item) => item.category.startsWith("key:"))) {
    keyCache.set(`${record.category.slice(4)}:${record.key_id}`, deserialize(record.value));
  }

  const keys = {
    get: async <T extends keyof SignalDataTypeMap>(type: T, ids: string[]) => {
      const result: { [id: string]: SignalDataTypeMap[T] } = {};
      for (const id of ids) {
        let value = keyCache.get(`${String(type)}:${id}`) as SignalDataTypeMap[T] | undefined;
        if (type === "app-state-sync-key" && value) {
          value = proto.Message.AppStateSyncKeyData.fromObject(value as object) as unknown as SignalDataTypeMap[T];
        }
        if (value) result[id] = value;
      }
      return result;
    },
    set: async (data: Partial<{ [T in keyof SignalDataTypeMap]: Record<string, SignalDataTypeMap[T] | null> }>) => {
      const updates: AuthRecord[] = [];
      for (const [type, entries] of Object.entries(data)) {
        for (const [id, value] of Object.entries(entries ?? {})) {
          const cacheKey = `${type}:${id}`;
          if (value === null) keyCache.delete(cacheKey);
          else keyCache.set(cacheKey, value);
          updates.push({ category: `key:${type}`, key_id: id, value: serialize(value) });
        }
      }
      if (updates.length) await api.saveAuth(accountId, updates);
    },
  };

  return {
    state: { creds, keys },
    saveCreds: () =>
      api.saveAuth(accountId, [
        { category: "creds", key_id: "default", value: serialize(creds) },
      ]),
  };
}
