import assert from "node:assert/strict";
import test from "node:test";

import { classifyEmqxPublishStatus } from "../../functions/dispatch-delivery/publish-response.js";

test("classifies only EMQX 200 as a delivered command", () => {
  assert.equal(classifyEmqxPublishStatus(200), "DELIVERED");
  assert.equal(
    classifyEmqxPublishStatus(202),
    "NO_MATCHING_SUBSCRIBERS",
  );
  assert.equal(classifyEmqxPublishStatus(400), "DEFINITIVE_REJECTION");
  assert.equal(classifyEmqxPublishStatus(401), "DEFINITIVE_REJECTION");
  assert.equal(classifyEmqxPublishStatus(429), "DEFINITIVE_REJECTION");
  assert.equal(classifyEmqxPublishStatus(408), "UNKNOWN");
  assert.equal(classifyEmqxPublishStatus(409), "UNKNOWN");
  assert.equal(classifyEmqxPublishStatus(425), "UNKNOWN");
  assert.equal(classifyEmqxPublishStatus(201), "UNKNOWN");
  assert.equal(classifyEmqxPublishStatus(204), "UNKNOWN");
  assert.equal(classifyEmqxPublishStatus(503), "UNKNOWN");
});
