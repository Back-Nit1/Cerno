import assert from "node:assert/strict";
import test from "node:test";

import { deletePost } from "../lib/post.mjs";

test("owner deletes post", () => assert.equal(deletePost({ id: 1 }, { ownerId: 1 }).deleted, true));
