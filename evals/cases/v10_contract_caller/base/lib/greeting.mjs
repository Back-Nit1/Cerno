import { publicUser } from "./user.mjs";

export function greeting(row) {
  const user = publicUser(row);
  return `Hello, ${user.name}!`;
}
