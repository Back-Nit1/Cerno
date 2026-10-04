import assert from "node:assert/strict";
import test from "node:test";

import { expiresAt } from "../lib/cache.mjs";

test("expiry is in the future", () => assert.ok(expiresAt(1000) > 1000));
