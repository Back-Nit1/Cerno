export function parseToken(value) {
  if (typeof value !== "string" || !/^[A-Za-z0-9_-]{6,128}$/.test(value)) {
    throw new Error("invalid token");
  }
  return value;
}
