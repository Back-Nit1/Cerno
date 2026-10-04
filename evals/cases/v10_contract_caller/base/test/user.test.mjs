import assert from "node:assert/strict";
import test from "node:test";

import { publicUser } from "../lib/user.mjs";

test("returns an object", () => assert.equal(typeof publicUser({ id: 1, name: "Ana" }), "object"));
