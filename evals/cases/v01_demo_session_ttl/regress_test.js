import assert from "node:assert/strict";
import test from "node:test";

import { createSession, isSessionValid } from "../lib/session.mjs";

test("login session remains valid after 31 minutes", () => {
  const started = 1_000;
  assert.equal(isSessionValid(createSession("user-1", started), started + 31 * 60 * 1000), true);
});
