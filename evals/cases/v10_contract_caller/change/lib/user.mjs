export function publicUser(row) {
  return { userId: row.id, displayName: row.name };
}
