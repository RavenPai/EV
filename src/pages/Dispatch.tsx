import { useMemo, useState } from "react";
import { AlertTriangle, ArrowRight, Bot, Check, Clock3, PackageCheck, RadioTower, Route, ShieldCheck, UserRound, X } from "lucide-react";
import { StatusPill } from "../components/StatusPill";
import { useApp } from "../context/AppContext";
import { getLocation } from "../data/demo";
import { cloudEnabled } from "../lib/supabase";
import type { Delivery } from "../types";

export function Dispatch() {
  const { deliveries, robots, approveDelivery, assignDelivery, dispatchDelivery, cancelDelivery, advanceDelivery } = useApp();
  const queue = useMemo(() => deliveries.filter((delivery) => !["COMPLETED", "CANCELLED", "FAILED"].includes(delivery.status)), [deliveries]);
  const [selectedId, setSelectedId] = useState(queue[0]?.id);
  const selected = queue.find((delivery) => delivery.id === selectedId) ?? queue[0];
  const [robotChoice, setRobotChoice] = useState(selected?.robotId ?? "robot-01");
  const [busy, setBusy] = useState(false);

  const run = async (action: () => Promise<void>) => {
    setBusy(true);
    try { await action(); } finally { setBusy(false); }
  };

  const nextAction = (delivery: Delivery) => {
    if (delivery.status === "REQUESTED") return { label: "Approve request", action: () => approveDelivery(delivery.id), icon: Check };
    if (delivery.status === "APPROVED") return { label: "Assign selected robot", action: () => assignDelivery(delivery.id, robotChoice), icon: Bot };
    if (delivery.status === "ASSIGNED") return { label: "Dispatch mission", action: () => dispatchDelivery(delivery.id), icon: RadioTower };
    if (cloudEnabled) return undefined;
    return { label: "Advance demo checkpoint", action: () => advanceDelivery(delivery.id), icon: ArrowRight };
  };
  const action = selected ? nextAction(selected) : undefined;

  return (
    <div className="dispatch-layout">
      <section className="panel dispatch-queue-panel">
        <div className="panel-heading dispatch-heading"><div><span className="eyebrow">Mission queue</span><h3>{queue.length} open deliveries</h3></div><span className="count-badge">Live</span></div>
        <div className="dispatch-filters"><button className="active">All</button><button>Approval</button><button>Assigned</button><button>In transit</button></div>
        <div className="dispatch-list">
          {queue.map((delivery) => (
            <button key={delivery.id} className={`dispatch-list-item ${selected?.id === delivery.id ? "selected" : ""}`} onClick={() => { setSelectedId(delivery.id); setRobotChoice(delivery.robotId ?? "robot-01"); }}>
              <div className="dispatch-list-top"><strong>{delivery.trackingCode}</strong><StatusPill value={delivery.priority} dot={false} /></div>
              <div className="dispatch-list-route"><i className="source-dot" />{getLocation(delivery.sourceId)?.shortName}<ArrowRight size={13} /><i className="destination-dot" />{getLocation(delivery.destinationId)?.shortName}</div>
              <div className="dispatch-list-bottom"><StatusPill value={delivery.status} /><span><Clock3 size={13} />{delivery.etaMinutes ?? 18} min</span></div>
            </button>
          ))}
        </div>
      </section>

      {selected ? (
        <section className="dispatch-detail-column">
          <article className="panel dispatch-detail-panel">
            <div className="panel-heading"><div><span className="eyebrow">Selected request</span><h2>{selected.trackingCode}</h2></div><StatusPill value={selected.status} /></div>
            <div className="dispatch-route-hero">
              <div><span className="route-number">A</span><small>Pickup</small><strong>{getLocation(selected.sourceId)?.name}</strong></div>
              <div className="route-hero-line"><Route size={19} /><span>420 m · about {selected.etaMinutes ?? 18} min</span></div>
              <div><span className="route-number route-number-end">B</span><small>Destination</small><strong>{getLocation(selected.destinationId)?.name}</strong></div>
            </div>
            <div className="dispatch-facts">
              <div><PackageCheck size={18} /><span>Package</span><strong>{selected.itemName}</strong><small>{selected.category} · {selected.weightKg} kg</small></div>
              <div><UserRound size={18} /><span>Recipient</span><strong>{selected.recipientName}</strong><small>{selected.recipientPhone}</small></div>
              <div><ShieldCheck size={18} /><span>Safety check</span><strong>Payload accepted</strong><small>Below 10 kg limit</small></div>
            </div>
            {selected.notes && <div className="operator-note"><strong>Operator note</strong><p>{selected.notes}</p></div>}
          </article>

          <article className="panel assignment-panel">
            <div className="panel-heading"><div><span className="eyebrow">Robot assignment</span><h3>Select a ready vehicle</h3></div><Bot size={22} className="heading-icon" /></div>
            <div className="robot-choice-grid">
              {robots.map((robot) => {
                const selectable = ["ONLINE", "BUSY"].includes(robot.status) && robot.battery >= 35;
                return (
                  <button key={robot.id} disabled={!selectable || !["APPROVED", "ASSIGNED"].includes(selected.status)} className={`robot-choice ${robotChoice === robot.id ? "selected" : ""}`} onClick={() => setRobotChoice(robot.id)}>
                    <div className={`robot-orb robot-${robot.status.toLowerCase()}`}><Bot size={21} /></div>
                    <div><strong>{robot.name}</strong><span>{robot.status} · {robot.battery}% battery</span></div>
                    {robotChoice === robot.id && <Check className="robot-choice-check" size={17} />}
                  </button>
                );
              })}
            </div>
            <div className="dispatch-safety-note"><AlertTriangle size={17} /><p>Dispatch sends a mission-level command. The Raspberry Pi plans movement locally; the web app never streams motor directions.</p></div>
            {cloudEnabled && selected.status === "DISPATCHED" && <div className="dispatch-safety-note"><RadioTower size={17} /><p>Command published. Waiting for the Raspberry Pi to acknowledge it and publish <code>MISSION_STARTED</code>.</p></div>}
            <div className="dispatch-actions">
              {["REQUESTED", "APPROVED"].includes(selected.status) && <button className="button button-danger-outline" onClick={() => void run(() => cancelDelivery(selected.id))}><X size={16} />Reject</button>}
              {action && <button className="button button-primary button-large" disabled={busy} onClick={() => void run(action.action)}><action.icon size={18} />{busy ? "Working…" : action.label}</button>}
            </div>
          </article>
        </section>
      ) : <section className="panel empty-state"><PackageCheck size={36} /><strong>The dispatch queue is clear</strong><p>New requests will appear here automatically.</p></section>}
    </div>
  );
}
