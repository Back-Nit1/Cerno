import assert from "node:assert/strict";
import test from "node:test";

import { greeting } from "../lib/greeting.mjs";

test("existing caller receives the public name", () => {
  assert.equal(greeting({ id: 1, name: "Ana" }), "Hello, Ana!");
});
