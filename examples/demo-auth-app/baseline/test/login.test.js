import assert from "node:assert/strict";
import { beforeEach, test } from "node:test";
import { login } from "../src/auth/login.js";
import { isSessionValid } from "../src/auth/session.js";
import { createUser, resetUsers } from "../src/auth/users.js";

beforeEach(() => {
  resetUsers();
  createUser("ada@example.com", "correct horse");
});

test("login succeeds with valid credentials", () => {
  const result = login("ada@example.com", "correct horse");
  assert.equal(result.ok, true);
  assert.ok(isSessionValid(result.session));
});

test("login rejects a wrong password", () => {
  assert.deepEqual(login("ada@example.com", "nope"), { ok: false, error: "invalid_credentials" });
});
