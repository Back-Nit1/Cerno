import assert from "node:assert/strict";
import test from "node:test";

import { acceptPayment } from "../lib/payment.mjs";

test("rejects negative payment", () => assert.throws(() => acceptPayment(-1), /invalid amount/));
