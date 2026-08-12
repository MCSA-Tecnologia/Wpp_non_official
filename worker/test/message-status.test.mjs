import assert from "node:assert/strict";
import test from "node:test";

import { normalizeMessageStatus } from "../dist/connectors/baileys.js";

test("Baileys ERROR status zero is persisted instead of discarded", () => {
  assert.equal(normalizeMessageStatus(0), 0);
});

test("message updates without a status do not become false errors", () => {
  assert.equal(normalizeMessageStatus(undefined), null);
  assert.equal(normalizeMessageStatus(null), null);
});

test("Baileys delivery and read statuses are preserved", () => {
  assert.equal(normalizeMessageStatus(2), 2);
  assert.equal(normalizeMessageStatus(3), 3);
  assert.equal(normalizeMessageStatus(4), 4);
  assert.equal(normalizeMessageStatus(5), 4);
});
