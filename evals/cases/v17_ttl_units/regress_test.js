import assert from "node:assert/strict";
import test from "node:test";

import { expiresAt } from "../lib/cache.mjs";

test("cache expires after five minutes", () => assert.equal(expiresAt(1000) - 1000, 300_000));
