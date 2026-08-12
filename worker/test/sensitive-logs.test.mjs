import assert from "node:assert/strict";
import test from "node:test";

import { installSensitiveLogFilters } from "../dist/sensitive-logs.js";

test("Signal session dumps are suppressed while normal logs remain", () => {
  const originalInfo = console.info;
  const captured = [];
  console.info = (...args) => captured.push(args);
  try {
    installSensitiveLogFilters();
    console.info("Closing session:", { privateKey: "must-not-be-logged" });
    console.info("normal message", { ok: true });
  } finally {
    console.info = originalInfo;
  }

  assert.deepEqual(captured, [["normal message", { ok: true }]]);
});
