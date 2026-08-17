import { describe, expect, it } from "vitest";
import { missionReceiptPresentation } from "../lib/mission-command";
import type { MissionCommand } from "../types";

const command = (status: MissionCommand["status"]): MissionCommand => ({
  id: "command-1",
  deliveryId: "delivery-1",
  robotId: "robot-01",
  status,
  issuedAt: "2026-08-17T07:00:00.000Z",
});

describe("Raspberry Pi delivery receipt presentation", () => {
  it("shows published commands as waiting for the Pi button", () => {
    expect(missionReceiptPresentation(command("PUBLISHED"))).toMatchObject({
      tone: "waiting",
      title: "Waiting for Raspberry Pi acknowledgment",
    });
  });

  it("presents the terminal command result as an acknowledgment", () => {
    expect(missionReceiptPresentation({
      ...command("COMPLETED"),
      acknowledgedAt: "2026-08-17T07:01:00.000Z",
    })).toMatchObject({
      tone: "success",
      title: "Acknowledged by Raspberry Pi",
    });
  });

  it("shows failed receipts as errors", () => {
    expect(missionReceiptPresentation({
      ...command("FAILED"),
      reason: "No matching subscriber",
    })).toEqual({
      tone: "error",
      title: "Raspberry Pi acknowledgment failed",
      detail: "No matching subscriber",
    });
  });
});
