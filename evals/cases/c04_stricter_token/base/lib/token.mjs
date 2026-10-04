export function parseToken(value) {
  if (typeof value !== "string" || value.length === 0) throw new Error("invalid token");
  return value;
}
