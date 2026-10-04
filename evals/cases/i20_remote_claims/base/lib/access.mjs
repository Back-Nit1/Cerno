export function isAdmin(claims) {
  return claims.role === "admin";
}
