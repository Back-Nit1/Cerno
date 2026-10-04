import assert from "node:assert/strict";
import test from "node:test";

import { parseToken } from "../lib/token.mjs";

test("parses a token", () => assert.equal(parseToken("abc123"), "abc123"));
