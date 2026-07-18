import { formatStatus } from "../data/demo";

const tones: Record<string, string> = {
  REQUESTED: "amber", APPROVED: "blue", ASSIGNED: "violet", DISPATCHED: "violet", TO_SOURCE: "cyan", AT_SOURCE: "cyan",
  PACKAGE_LOADED: "lime", TO_DESTINATION: "cyan", AT_DESTINATION: "lime", DELIVERED: "green",
  RETURNING: "blue", COMPLETED: "green", PAUSED: "amber", FAILED: "red", CANCELLED: "gray",
  ONLINE: "green", BUSY: "cyan", CHARGING: "violet", OFFLINE: "gray", FAULT: "red",
  IDLE: "gray", AUTO: "cyan", MANUAL: "violet", ESTOP: "red", OK: "green", WARNING: "amber",
  NORMAL: "gray", HIGH: "amber", URGENT: "red",
};

export function StatusPill({ value, dot = true }: { value: string; dot?: boolean }) {
  return (
    <span className={`status-pill status-${tones[value] ?? "gray"}`}>
      {dot && <span className="status-dot" />}
      {formatStatus(value)}
    </span>
  );
}
