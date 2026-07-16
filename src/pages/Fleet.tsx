import { useMemo, useState } from "react";
import { AlertOctagon, BatteryCharging, Bot, Camera, Gauge, Pause, Play, Radio, RotateCcw, ScanLine, ShieldAlert, Signal, Thermometer, Wifi } from "lucide-react";
import { CampusMap } from "../components/CampusMap";
import { StatusPill } from "../components/StatusPill";
import { useApp } from "../context/AppContext";

export function Fleet() {
  const { robots, deliveries, sendRobotCommand } = useApp();
  const [selectedId, setSelectedId] = useState(robots[0]?.id);
  const [confirmEstop, setConfirmEstop] = useState(false);
  const selected = robots.find((robot) => robot.id === selectedId) ?? robots[0];
  const mission = useMemo(() => deliveries.find((delivery) => delivery.id === selected?.currentDeliveryId), [deliveries, selected]);
  const [sending, setSending] = useState(false);
  const command = async (value: "PAUSE" | "RESUME" | "RETURN_HOME" | "ESTOP") => {
    setSending(true);
    try { await sendRobotCommand(selected.id, value); } finally { setSending(false); setConfirmEstop(false); }
  };

  return (
    <div className="fleet-page">
      <section className="fleet-card-grid">
        {robots.map((robot) => (
          <button key={robot.id} className={`fleet-card ${selected.id === robot.id ? "selected" : ""}`} onClick={() => setSelectedId(robot.id)}>
            <div className="fleet-card-top"><div className={`robot-orb robot-${robot.status.toLowerCase()}`}><Bot size={23} /></div><StatusPill value={robot.status} /></div>
            <h3>{robot.name}</h3><p>{robot.model}</p>
            <div className="fleet-card-stats"><div><BatteryCharging size={16} /><span>Battery</span><strong>{robot.battery}%</strong></div><div><Signal size={16} /><span>Signal</span><strong>{robot.signal}%</strong></div><div><Gauge size={16} /><span>Speed</span><strong>{robot.speedMps.toFixed(2)}</strong></div></div>
            <div className="battery-track"><i style={{ width: `${robot.battery}%` }} /></div>
          </button>
        ))}
      </section>

      <section className="fleet-main-grid">
        <article className="panel fleet-map-panel">
          <div className="panel-heading"><div><span className="eyebrow">Live position</span><h3>{selected.name} on campus</h3></div><div className="live-badge"><i /> Updated now</div></div>
          <CampusMap robot={selected} delivery={mission} />
        </article>

        <article className="panel telemetry-panel">
          <div className="panel-heading"><div><span className="eyebrow">Vehicle telemetry</span><h3>Health and sensors</h3></div><StatusPill value={selected.mode} /></div>
          <div className="telemetry-highlight"><div className="battery-ring" style={{ "--battery": `${selected.battery * 3.6}deg` } as React.CSSProperties}><div><BatteryCharging size={22} /><strong>{selected.battery}%</strong><span>Battery</span></div></div><div className="telemetry-summary"><span>Estimated runtime</span><strong>{Math.round(selected.battery * 0.72)} minutes</strong><small>12.3 V · 2.1 A · power stable</small></div></div>
          <div className="sensor-grid">
            <div><ScanLine size={18} /><span>2D LiDAR</span><strong>{selected.lidar}</strong><i className={`sensor-${selected.lidar.toLowerCase()}`} /></div>
            <div><Camera size={18} /><span>Camera</span><strong>{selected.camera}</strong><i className={`sensor-${selected.camera.toLowerCase()}`} /></div>
            <div><Radio size={18} /><span>ESP32 link</span><strong>{selected.esp32}</strong><i className={`sensor-${selected.esp32.toLowerCase()}`} /></div>
            <div><Thermometer size={18} /><span>Motor temp.</span><strong>{selected.motorTempC}°C</strong><i className="sensor-ok" /></div>
            <div><Wifi size={18} /><span>Cloud signal</span><strong>{selected.signal}%</strong><i className="sensor-ok" /></div>
            <div><Gauge size={18} /><span>Velocity</span><strong>{selected.speedMps} m/s</strong><i className="sensor-ok" /></div>
          </div>
        </article>
      </section>

      <section className="fleet-lower-grid">
        <article className="panel command-panel">
          <div className="panel-heading"><div><span className="eyebrow">Mission controls</span><h3>Authorized safe commands</h3></div><ShieldAlert size={21} className="heading-icon" /></div>
          <p className="command-intro">Controls create signed, expiring commands. The robot confirms each command before the interface changes state.</p>
          <div className="command-buttons">
            {selected.mode === "PAUSED" ? <button className="command-button command-resume" disabled={sending} onClick={() => void command("RESUME")}><span><Play size={20} /></span><div><strong>Resume mission</strong><small>Continue local navigation</small></div></button> : <button className="command-button" disabled={sending || selected.mode === "ESTOP"} onClick={() => void command("PAUSE")}><span><Pause size={20} /></span><div><strong>Pause safely</strong><small>Stop and retain mission state</small></div></button>}
            <button className="command-button" disabled={sending || selected.mode === "ESTOP"} onClick={() => void command("RETURN_HOME")}><span><RotateCcw size={20} /></span><div><strong>Return home</strong><small>Navigate to robot station</small></div></button>
            <button className="command-button command-estop" disabled={sending} onClick={() => setConfirmEstop(true)}><span><AlertOctagon size={20} /></span><div><strong>Emergency stop</strong><small>Stop motion and require reset</small></div></button>
          </div>
          <div className="manual-control-lock"><ShieldAlert size={17} /><div><strong>Continuous motor control is disabled on the public web app.</strong><p>Manual driving must use an authenticated nearby operator on the robot's local network.</p></div></div>
        </article>

        <article className="panel current-assignment-panel">
          <div className="panel-heading"><div><span className="eyebrow">Current assignment</span><h3>{mission?.trackingCode ?? "Standing by"}</h3></div>{mission && <StatusPill value={mission.status} />}</div>
          {mission ? <><div className="assignment-progress"><div><span>Progress</span><strong>{mission.progress}%</strong></div><div className="progress-track"><i style={{ width: `${mission.progress}%` }} /></div></div><dl><div><dt>Package</dt><dd>{mission.itemName}</dd></div><div><dt>ETA</dt><dd>{mission.etaMinutes} minutes</dd></div><div><dt>Command owner</dt><dd>Mission manager</dd></div><div><dt>Last heartbeat</dt><dd>Just now</dd></div></dl></> : <div className="empty-state"><Bot size={30} /><strong>Robot is available</strong><p>Assign it from the dispatch center.</p></div>}
        </article>
      </section>

      {confirmEstop && <div className="modal-layer"><button className="modal-backdrop" onClick={() => setConfirmEstop(false)} aria-label="Close confirmation" /><div className="confirm-modal"><div className="danger-icon"><AlertOctagon size={28} /></div><h2>Emergency-stop {selected.name}?</h2><p>This requests an immediate controlled stop and changes the robot to ESTOP. A nearby operator must inspect and reset the vehicle.</p><div><button className="button button-ghost" onClick={() => setConfirmEstop(false)}>Cancel</button><button className="button button-danger" onClick={() => void command("ESTOP")}>Confirm emergency stop</button></div></div></div>}
    </div>
  );
}
