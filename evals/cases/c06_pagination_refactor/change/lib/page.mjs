export function page(items, offset, limit) {
  const start = Math.max(0, offset);
  const end = start + Math.max(0, limit);
  return items.slice(start, end);
}
