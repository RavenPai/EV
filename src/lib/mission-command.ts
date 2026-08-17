import type { MissionCommand } from "../types";

export type MissionReceiptTone = "waiting" | "success" | "error";

export interface MissionReceiptPresentation {
  tone: MissionReceiptTone;
  title: string;
  detail: string;
}

export const missionReceiptPresentation = (
  command: MissionCommand | undefined,
): MissionReceiptPresentation | undefined => {
  if (!command) return undefined;

  if (["ACKNOWLEDGED", "COMPLETED"].includes(command.status)) {
    return {
      tone: "success",
      title: "Acknowledged by Raspberry Pi",
      detail: command.acknowledgedAt
        ? `Received ${new Date(command.acknowledgedAt).toLocaleString()}`
        : "The Raspberry Pi operator confirmed the delivery information.",
    };
  }

  if (["REJECTED", "FAILED", "EXPIRED"].includes(command.status)) {
    return {
      tone: "error",
      title: `Raspberry Pi acknowledgment ${command.status.toLowerCase()}`,
      detail: command.reason || "Send the delivery again after checking the Pi connection.",
    };
  }

  return {
    tone: "waiting",
    title: command.status === "PUBLISH_UNKNOWN"
      ? "Verifying delivery publish"
      : "Waiting for Raspberry Pi acknowledgment",
    detail: command.status === "PUBLISH_UNKNOWN"
      ? "The broker result is uncertain. Do not send a duplicate command."
      : "The delivery information was sent. Press Acknowledge on the Raspberry Pi display.",
  };
};
