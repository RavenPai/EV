import { Check, Circle } from "lucide-react";
import { statusOrder, formatStatus } from "../data/demo";
import type { DeliveryStatus } from "../types";

const displaySteps: DeliveryStatus[] = ["REQUESTED", "ASSIGNED", "DISPATCHED", "TO_SOURCE", "PACKAGE_LOADED", "TO_DESTINATION", "DELIVERED", "COMPLETED"];

export function DeliveryTimeline({ status, compact = false }: { status: DeliveryStatus; compact?: boolean }) {
  const currentIndex = statusOrder.indexOf(status as (typeof statusOrder)[number]);
  const isTerminalError = ["FAILED", "CANCELLED"].includes(status);

  return (
    <div className={`timeline ${compact ? "timeline-compact" : ""}`}>
      {displaySteps.map((step, index) => {
        const stepIndex = statusOrder.indexOf(step as (typeof statusOrder)[number]);
        const complete = !isTerminalError && currentIndex >= stepIndex;
        const active = status === step || (step === "ASSIGNED" && status === "APPROVED");
        return (
          <div className={`timeline-step ${complete ? "is-complete" : ""} ${active ? "is-active" : ""}`} key={step}>
            <div className="timeline-marker">{complete ? <Check size={13} strokeWidth={3} /> : <Circle size={10} />}</div>
            <div>
              <strong>{formatStatus(step)}</strong>
              {!compact && <span>{index === 0 ? "Request logged" : index === displaySteps.length - 1 ? "Mission closed" : "Mission checkpoint"}</span>}
            </div>
          </div>
        );
      })}
    </div>
  );
}
