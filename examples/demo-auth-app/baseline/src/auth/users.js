import { randomBytes, scryptSync, timingSafeEqual } from "node:crypto";

const users = new Map();

export function hashPassword(password, salt = randomBytes(16).toString("hex")) {
  return `${salt}:${scryptSync(password, salt, 32).toString("hex")}`;
}

export function verifyPassword(password, stored) {
  const [salt, hash] = stored.split(":");
  return timingSafeEqual(scryptSync(password, salt, 32), Buffer.from(hash, "hex"));
}

export function createUser(email, password) {
  const user = { id: randomBytes(8).toString("hex"), email, passwordHash: hashPassword(password) };
  users.set(email, user);
  return user;
}

export function findUserByEmail(email) {
  return users.get(email) ?? null;
}

export function resetUsers() {
  users.clear();
}
