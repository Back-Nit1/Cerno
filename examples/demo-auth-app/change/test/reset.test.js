import assert from "node:assert/strict";
import { beforeEach, test } from "node:test";
import { login } from "../src/auth/login.js";
import { RESET_TOKEN_TTL_MS, requestPasswordReset, resetPassword } from "../src/auth/reset.js";
import { createUser, resetUsers } from "../src/auth/users.js";

beforeEach(() => {
  resetUsers();
  createUser("ada@example.com", "old password");
});

test("reset token lets the user set a new password", () => {
  const token = requestPasswordReset("ada@example.com");
  assert.equal(resetPassword(token, "new password"), true);
  assert.equal(login("ada@example.com", "new password").ok, true);
});

test("reset tokens expire after 30 minutes", () => {
  const now = Date.now();
  const token = requestPasswordReset("ada@example.com", now);
  assert.equal(RESET_TOKEN_TTL_MS, 30 * 60 * 1000);
  assert.equal(resetPassword(token, "new password", now + RESET_TOKEN_TTL_MS + 1), false);
});

test("unknown accounts get no token", () => {
  assert.equal(requestPasswordReset("nobody@example.com"), null);
});
