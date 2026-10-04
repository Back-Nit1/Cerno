import assert from "node:assert/strict";
import test from "node:test";

import { total } from "../lib/order.mjs";

test("totals member orders", () => {
  assert.equal(total({ member: true, items: [{ price: 10, quantity: 2 }] }), 18);
});
