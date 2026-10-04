import assert from "node:assert/strict";
import test from "node:test";

import { deletePost } from "../lib/post.mjs";

test("non-owner cannot delete", () => {
  assert.throws(() => deletePost({ id: 2 }, { ownerId: 1 }), /forbidden/);
});
