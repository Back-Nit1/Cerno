import { randomUUID } from "node:crypto";

// Sessions stay valid for 24 hours after login.
export const SESSION_TTL_MS = 24 * 60 * 60 * 1000;

export function createSession(userId, now = Date.now()) {
  return { id: randomUUID(), userId, createdAt: now, expiresAt: now + SESSION_TTL_MS };
}

export function isSessionValid(session, now = Date.now()) {
  return Boolean(session) && session.expiresAt > now;
}
