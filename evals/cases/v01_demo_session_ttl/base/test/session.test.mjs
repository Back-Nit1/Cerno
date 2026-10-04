import assert from "node:assert/strict";
import test from "node:test";

import { createSession, isSessionValid } from "../lib/session.mjs";

test("new session is valid", () => assert.equal(isSessionValid(createSession("user-1", 1000), 1000), true));
