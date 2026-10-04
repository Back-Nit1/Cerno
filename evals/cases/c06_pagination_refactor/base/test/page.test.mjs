import assert from "node:assert/strict";
import test from "node:test";

import { page } from "../lib/page.mjs";

test("returns requested page", () => assert.deepEqual(page([1, 2, 3, 4], 2, 2), [3, 4]));
