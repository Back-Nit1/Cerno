import assert from "node:assert/strict";
import test from "node:test";

import { editPost } from "../lib/post.mjs";

test("owner edits post", () => {
  assert.equal(editPost({ id: 1 }, { ownerId: 1, title: "old" }, "new").title, "new");
});
test("stranger is rejected", () => {
  assert.throws(() => editPost({ id: 2 }, { ownerId: 1 }, "new"), /forbidden/);
});
