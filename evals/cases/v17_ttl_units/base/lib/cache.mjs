import { TTL_SECONDS } from "./config.mjs";

export function expiresAt(now) {
  return now + TTL_SECONDS * 1000;
}
