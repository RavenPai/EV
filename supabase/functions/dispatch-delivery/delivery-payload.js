const text = (value) => value == null ? "" : String(value);

/**
 * Build the human-readable delivery snapshot sent to the Raspberry Pi.
 *
 * The database row remains the source of truth. The snapshot lets the Pi show
 * the delivery without receiving Supabase credentials or making another HTTP
 * request.
 */
export const buildAcknowledgementOnlyDeliveryPayload = (
  delivery,
  sourceLocation,
  destinationLocation,
) => ({
  sourceLocationId: text(delivery.source_id),
  destinationLocationId: text(delivery.destination_id),
  mapVersion: "miit-campus-v1",
  deliveryId: text(delivery.id),
  deliveryMode: "ACKNOWLEDGEMENT_ONLY",
  delivery: {
    trackingCode: text(delivery.tracking_code),
    requesterName: text(delivery.requester_name),
    requesterEmail: text(delivery.requester_email),
    recipientName: text(delivery.recipient_name),
    recipientPhone: text(delivery.recipient_phone),
    sourceName: text(sourceLocation?.name ?? delivery.source_id),
    destinationName: text(destinationLocation?.name ?? delivery.destination_id),
    itemName: text(delivery.item_name),
    category: text(delivery.category),
    weightKg: Number(delivery.weight_kg),
    priority: text(delivery.priority),
    notes: text(delivery.notes),
  },
});
