export function isAdmin(claims) {
  return claims.groups.includes("administrators");
}
