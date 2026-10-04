import assert from "node:assert/strict";
import test from "node:test";

import { acceptPayment } from "../lib/payment.mjs";

test("accepts positive payment", () => assert.equal(acceptPayment(12).amount, 12));
