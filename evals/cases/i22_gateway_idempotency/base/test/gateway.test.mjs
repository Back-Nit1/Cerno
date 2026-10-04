import assert from "node:assert/strict";
import test from "node:test";

import { paymentHeaders } from "../lib/gateway.mjs";

test("adds one header", () => assert.equal(Object.keys(paymentHeaders("order-1")).length, 1));
