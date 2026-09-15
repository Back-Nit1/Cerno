import { randomUUID } from "node:crypto";

// Sessions and reset tokens share a 30 minute lifetime.
export const SESSION_TTL_MS = 30 * 60 * 1000;

export function createSession(userId, now = Date.now()) {
  return { id: randomUUID(), userId, createdAt: now, expiresAt: now + SESSION_TTL_MS };
}

export function isSessionValid(session, now = Date.now()) {
  return Boolean(session) && session.expiresAt > now;
}
