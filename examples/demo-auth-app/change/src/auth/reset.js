import { randomBytes } from "node:crypto";
import { sendMail } from "./mailer.js";
import { findUserByEmail, hashPassword } from "./users.js";

export const RESET_TOKEN_TTL_MS = 30 * 60 * 1000;

const resetTokens = new Map();

export function requestPasswordReset(email, now = Date.now()) {
  const user = findUserByEmail(email);
  if (!user) return null; // do not reveal whether the account exists
  const token = randomBytes(32).toString("hex");
  resetTokens.set(token, { email, expiresAt: now + RESET_TOKEN_TTL_MS });
  sendMail(email, "Reset your password", `Use this token within 30 minutes: ${token}`);
  return token;
}

export function resetPassword(token, newPassword, now = Date.now()) {
  const entry = resetTokens.get(token);
  resetTokens.delete(token);
  if (!entry || entry.expiresAt <= now) return false;
  findUserByEmail(entry.email).passwordHash = hashPassword(newPassword);
  return true;
}
