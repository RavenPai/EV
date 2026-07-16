import { Link } from "react-router-dom";
import {
  ArrowRight, BatteryCharging, Bot, CheckCircle2, Clock3, MapPin, PackageCheck,
  PackagePlus, RadioTower, Route, ShieldCheck, Signal, Sparkles,
} from "lucide-react";
import { CampusMap } from "../components/CampusMap";
import { DeliveryTimeline } from "../components/DeliveryTimeline";
import { StatusPill } from "../components/StatusPill";
import { useApp } from "../context/AppContext";
import { getLocation } from "../data/demo";

export function Dashboard() {
  const { deliveries, robots, role } = useApp();
  const activeDeliveries = deliveries.filter((delivery) => !["COMPLETED", "CANCELLED", "FAILED", "REQUESTED"].includes(delivery.status));
  const primaryDelivery = activeDeliveries.find((delivery) => delivery.status === "TO_DESTINATION") ?? activeDeliveries[0];
  const primaryRobot = robots.find((robot) => robot.id === primaryDelivery?.robotId) ?? robots[0];
  const completedToday = deliveries.filter((delivery) => delivery.status === "COMPLETED").length;
  const availableRobots = robots.filter((robot) => robot.status === "ONLINE").length;
  const queue = deliveries.filter((delivery) => ["REQUESTED", "APPROVED", "ASSIGNED"].includes(delivery.status));

  return (
    <div className="dashboard-page">
      <section className="welcome-strip">
        <div>
          <span className="eyebrow"><Sparkles size={14} /> Thursday, 16 July</span>
          <h2>{role === "USER" ? "Where should we deliver today?" : "The campus fleet is operating normally."}</h2>
          <p>{role === "USER" ? "Request a robot delivery and follow every checkpoint from pickup to handoff." : "All safety-critical robot systems are reporting. One mission is currently in transit."}</p>
        </div>
        <Link className="button button-primary" to={role === "USER" ? "/new-delivery" : "/dispatch"}>
          {role === "USER" ? <PackagePlus size={18} /> : <RadioTower size={18} />}
          {role === "USER" ? "New delivery" : "Open dispatch"}<ArrowRight size={17} />
        </Link>
      </section>

      <section className="metric-grid">
        <article className="metric-card metric-dark">
          <div className="metric-icon"><Route size={20} /></div>
          <span>Active missions</span><strong>{activeDeliveries.length}</strong>
          <small><i className="pulse-dot" /> {primaryDelivery ? `${primaryDelivery.trackingCode} in transit` : "No active route"}</small>
        </article>
        <article className="metric-card">
          <div className="metric-icon icon-mint"><Bot size={20} /></div>
          <span>Robots ready</span><strong>{availableRobots}<em>/ {robots.length}</em></strong>
          <small><CheckCircle2 size={14} /> Safety links responding</small>
        </article>
        <article className="metric-card">
          <div className="metric-icon icon-lilac"><PackageCheck size={20} /></div>
          <span>Completed today</span><strong>{completedToday}</strong>
          <small><span className="trend-up">↑ 18%</span> from yesterday</small>
        </article>
        <article className="metric-card">
          <div className="metric-icon icon-sand"><Clock3 size={20} /></div>
          <span>Average mission</span><strong>16<em> min</em></strong>
          <small>Within the 20 min target</small>
        </article>
      </section>

      <section className="dashboard-main-grid">
        <article className="panel map-panel">
          <div className="panel-heading">
            <div><span className="eyebrow">Live campus map</span><h3>Robot route and checkpoints</h3></div>
            <Link to="/fleet" className="text-link">View fleet <ArrowRight size={15} /></Link>
          </div>
          <CampusMap delivery={primaryDelivery} robot={primaryRobot} />
        </article>

        <article className="panel mission-panel">
          <div className="panel-heading">
            <div><span className="eyebrow">Current mission</span><h3>{primaryDelivery?.trackingCode ?? "No active mission"}</h3></div>
            {primaryDelivery && <StatusPill value={primaryDelivery.status} />}
          </div>
          {primaryDelivery ? (
            <>
              <div className="route-summary">
                <div><i className="route-point source-point" /><span>Pickup</span><strong>{getLocation(primaryDelivery.sourceId)?.shortName}</strong></div>
                <div className="route-line"><span>{primaryDelivery.etaMinutes} min</span></div>
                <div><i className="route-point destination-point" /><span>Deliver to</span><strong>{getLocation(primaryDelivery.destinationId)?.shortName}</strong></div>
              </div>
              <div className="mission-meta-grid">
                <div><Bot size={17} /><span>Robot</span><strong>{primaryRobot?.name}</strong></div>
                <div><BatteryCharging size={17} /><span>Battery</span><strong>{primaryRobot?.battery}%</strong></div>
                <div><Signal size={17} /><span>Signal</span><strong>{primaryRobot?.signal}%</strong></div>
              </div>
              <div className="progress-block"><div><span>Mission progress</span><strong>{primaryDelivery.progress}%</strong></div><div className="progress-track"><i style={{ width: `${primaryDelivery.progress}%` }} /></div></div>
              <DeliveryTimeline status={primaryDelivery.status} compact />
              <Link to="/deliveries" className="button button-secondary button-full">Open mission details <ArrowRight size={16} /></Link>
            </>
          ) : <div className="empty-state"><Bot size={32} /><strong>Fleet is standing by</strong><p>Dispatch a delivery to begin.</p></div>}
        </article>
      </section>

      <section className="dashboard-lower-grid">
        <article className="panel queue-panel">
          <div className="panel-heading"><div><span className="eyebrow">Next actions</span><h3>Dispatch queue</h3></div><span className="count-badge">{queue.length} waiting</span></div>
          <div className="compact-list">
            {queue.slice(0, 3).map((delivery) => (
              <div className="compact-row" key={delivery.id}>
                <div className="package-avatar">{delivery.itemName.charAt(0)}</div>
                <div className="grow"><strong>{delivery.trackingCode}</strong><span>{getLocation(delivery.sourceId)?.shortName} → {getLocation(delivery.destinationId)?.shortName}</span></div>
                <StatusPill value={delivery.status} dot={false} />
              </div>
            ))}
          </div>
          <Link className="text-link panel-link" to="/dispatch">Manage queue <ArrowRight size={15} /></Link>
        </article>

        <article className="panel fleet-mini-panel">
          <div className="panel-heading"><div><span className="eyebrow">Fleet readiness</span><h3>Robot health</h3></div><ShieldCheck size={22} className="heading-icon" /></div>
          <div className="robot-readiness-list">
            {robots.map((robot) => (
              <div className="robot-readiness" key={robot.id}>
                <div className={`robot-orb robot-${robot.status.toLowerCase()}`}><Bot size={18} /></div>
                <div className="grow"><strong>{robot.name}</strong><span>{robot.mode} · {robot.status}</span></div>
                <div className="battery-mini"><span>{robot.battery}%</span><div><i style={{ width: `${robot.battery}%` }} /></div></div>
              </div>
            ))}
          </div>
        </article>

        <article className="panel safety-panel">
          <div className="safety-visual"><ShieldCheck size={30} /><span>5/5</span></div>
          <span className="eyebrow">Safety layer</span><h3>All checks passing</h3>
          <p>ESP32 watchdog, LiDAR stop zone, E-stop, motor feedback and cloud heartbeat are reporting normally.</p>
          <div className="safety-checks"><span><i /> Local stop</span><span><i /> Command TTL</span><span><i /> Audit log</span></div>
        </article>
      </section>
    </div>
  );
}
