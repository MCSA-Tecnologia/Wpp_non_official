import assert from "node:assert/strict";
import test from "node:test";

import { persistAckWithRetry } from "../dist/ack-retry.js";

test("ACK persistence retries an early provider-id race", async () => {
  let calls = 0;
  const delays = [];
  await persistAckWithRetry(
    async () => {
      calls += 1;
      if (calls < 3) throw new Error("API 404: provider id not stored yet");
    },
    { sleep: async (ms) => delays.push(ms) },
  );

  assert.equal(calls, 3);
  assert.deepEqual(delays, [250, 500]);
});

test("ACK persistence fails after the bounded retry budget", async () => {
  let calls = 0;
  await assert.rejects(
    persistAckWithRetry(
      async () => {
        calls += 1;
        throw new Error("offline");
      },
      { attempts: 3, sleep: async () => undefined },
    ),
    /offline/,
  );
  assert.equal(calls, 3);
});
