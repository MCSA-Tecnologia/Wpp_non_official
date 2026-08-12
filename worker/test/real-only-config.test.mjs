import assert from "node:assert/strict";
import test from "node:test";

import { config } from "../dist/config.js";

test("runtime configuration has no simulated connector mode", () => {
  assert.equal("connectorMode" in config, false);
});
