import { clearAttempts, isRateLimited, recordFailedAttempt } from "./rateLimit.js";
import { createSession } from "./session.js";
import { findUserByEmail, verifyPassword } from "./users.js";

export function authenticatePassword(email, password) {
  const user = findUserByEmail(email);
  return user && verifyPassword(password, user.passwordHash) ? user : null;
}

export function login(email, password, now = Date.now()) {
  if (isRateLimited(email)) return { ok: false, error: "rate_limited" };
  const user = authenticatePassword(email, password);
  if (!user) {
    recordFailedAttempt(email);
    return { ok: false, error: "invalid_credentials" };
  }
  clearAttempts(email);
  return { ok: true, session: createSession(user.id, now) };
}
