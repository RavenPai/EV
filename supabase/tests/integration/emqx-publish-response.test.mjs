import assert from "node:assert/strict";
import test from "node:test";

import { buildAcknowledgementOnlyDeliveryPayload } from "../../functions/dispatch-delivery/delivery-payload.js";
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

test("builds the complete Raspberry Pi delivery-display snapshot", () => {
  const payload = buildAcknowledgementOnlyDeliveryPayload(
    {
      id: "11111111-1111-4111-8111-111111111111",
      tracking_code: "MIIT-2001",
      requester_name: "Campus User",
      requester_email: "user@example.edu",
      recipient_name: "Library Desk",
      recipient_phone: "+95 9000000000",
      source_id: "loc-fcs",
      destination_id: "loc-library",
      item_name: "Documents",
      category: "Documents",
      weight_kg: "1.25",
      priority: "HIGH",
      notes: "Handle carefully",
    },
    { id: "loc-fcs", name: "Faculty of Computer Science" },
    { id: "loc-library", name: "MIIT Library" },
  );

  assert.deepEqual(payload, {
    sourceLocationId: "loc-fcs",
    destinationLocationId: "loc-library",
    mapVersion: "miit-campus-v1",
    deliveryId: "11111111-1111-4111-8111-111111111111",
    deliveryMode: "ACKNOWLEDGEMENT_ONLY",
    delivery: {
      trackingCode: "MIIT-2001",
      requesterName: "Campus User",
      requesterEmail: "user@example.edu",
      recipientName: "Library Desk",
      recipientPhone: "+95 9000000000",
      sourceName: "Faculty of Computer Science",
      destinationName: "MIIT Library",
      itemName: "Documents",
      category: "Documents",
      weightKg: 1.25,
      priority: "HIGH",
      notes: "Handle carefully",
    },
  });
});
