import assert from "node:assert/strict";
import test from "node:test";

import { nextCursor } from "../lib/vendor.mjs";

test("missing cursor ends pagination", () => assert.equal(nextCursor({}), undefined));
