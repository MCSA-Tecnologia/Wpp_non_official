const delay = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

export async function persistAckWithRetry(
  persist: () => Promise<void>,
  options: {
    attempts?: number;
    initialDelayMs?: number;
    sleep?: (ms: number) => Promise<unknown>;
  } = {},
): Promise<void> {
  const attempts = options.attempts ?? 6;
  const initialDelayMs = options.initialDelayMs ?? 250;
  const sleep = options.sleep ?? delay;
  let lastError: unknown;
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    try {
      await persist();
      return;
    } catch (error) {
      lastError = error;
      if (attempt + 1 < attempts) {
        await sleep(Math.min(5_000, initialDelayMs * 2 ** attempt));
      }
    }
  }
  throw lastError;
}
