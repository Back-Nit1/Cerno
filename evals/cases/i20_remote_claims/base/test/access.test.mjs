import assert from "node:assert/strict";
import test from "node:test";

import { isAdmin } from "../lib/access.mjs";

test("ordinary user is denied", () => assert.equal(isAdmin({ role: "user", groups: [] }), false));
