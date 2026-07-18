import { useMemo, useState } from "react";
import { CalendarDays, ChevronRight, Clock3, Filter, MapPin, PackageOpen, Search, X } from "lucide-react";
import { DeliveryTimeline } from "../components/DeliveryTimeline";
import { StatusPill } from "../components/StatusPill";
import { useApp } from "../context/AppContext";
import { getLocation } from "../data/demo";
import type { Delivery } from "../types";

type FilterMode = "ALL" | "ACTIVE" | "WAITING" | "COMPLETED";

export function Deliveries() {
  const { deliveries, cancelDelivery } = useApp();
  const [filter, setFilter] = useState<FilterMode>("ALL");
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState<Delivery>();
  const visible = useMemo(() => deliveries.filter((delivery) => {
    const query = search.toLowerCase();
    const matchesSearch = !query || [delivery.trackingCode, delivery.itemName, delivery.recipientName].some((value) => value.toLowerCase().includes(query));
    const matchesFilter = filter === "ALL" ||
      (filter === "ACTIVE" && ["ASSIGNED", "DISPATCHED", "TO_SOURCE", "AT_SOURCE", "PACKAGE_LOADED", "TO_DESTINATION", "AT_DESTINATION", "DELIVERED", "RETURNING"].includes(delivery.status)) ||
      (filter === "WAITING" && ["REQUESTED", "APPROVED"].includes(delivery.status)) ||
      (filter === "COMPLETED" && ["COMPLETED", "CANCELLED"].includes(delivery.status));
    return matchesSearch && matchesFilter;
  }), [deliveries, filter, search]);

  return (
    <div className="deliveries-page">
      <section className="panel table-panel">
        <div className="table-toolbar">
          <div className="filter-tabs">{(["ALL", "ACTIVE", "WAITING", "COMPLETED"] as FilterMode[]).map((value) => <button key={value} className={filter === value ? "active" : ""} onClick={() => setFilter(value)}>{value.charAt(0) + value.slice(1).toLowerCase()}<span>{value === "ALL" ? deliveries.length : ""}</span></button>)}</div>
          <div className="toolbar-actions"><label className="search-box"><Search size={17} /><input placeholder="Search deliveries" value={search} onChange={(event) => setSearch(event.target.value)} /></label><button className="icon-button"><Filter size={18} /></button></div>
        </div>
        <div className="responsive-table-wrap">
          <table className="data-table">
            <thead><tr><th>Delivery</th><th>Route</th><th>Package</th><th>Robot</th><th>Status</th><th>Created</th><th aria-label="Actions" /></tr></thead>
            <tbody>
              {visible.map((delivery) => (
                <tr key={delivery.id} onClick={() => setSelected(delivery)}>
                  <td><strong>{delivery.trackingCode}</strong><span>{delivery.recipientName}</span></td>
                  <td><div className="table-route"><i className="source-dot" />{getLocation(delivery.sourceId)?.shortName}<span>→</span><i className="destination-dot" />{getLocation(delivery.destinationId)?.shortName}</div></td>
                  <td><strong>{delivery.itemName}</strong><span>{delivery.weightKg} kg · {delivery.category}</span></td>
                  <td>{delivery.robotId ? <strong>{delivery.robotId.replace("robot-", "Rover 0")}</strong> : <span className="muted">Not assigned</span>}</td>
                  <td><StatusPill value={delivery.status} /></td>
                  <td><span>{new Date(delivery.createdAt).toLocaleDateString([], { month: "short", day: "numeric" })}</span><small>{new Date(delivery.createdAt).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</small></td>
                  <td><button className="row-action" aria-label={`View ${delivery.trackingCode}`}><ChevronRight size={18} /></button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {visible.length === 0 && <div className="empty-state table-empty"><PackageOpen size={32} /><strong>No deliveries found</strong><p>Try changing the search or status filter.</p></div>}
      </section>

      {selected && (
        <div className="drawer-layer">
          <button className="drawer-backdrop" onClick={() => setSelected(undefined)} aria-label="Close details" />
          <aside className="details-drawer">
            <div className="drawer-header"><div><span className="eyebrow">Delivery details</span><h2>{selected.trackingCode}</h2></div><button className="icon-button" onClick={() => setSelected(undefined)}><X size={20} /></button></div>
            <div className="drawer-status"><StatusPill value={selected.status} /><StatusPill value={selected.priority} dot={false} /></div>
            <div className="drawer-route">
              <div><i className="route-point source-point" /><span>Pickup</span><strong>{getLocation(selected.sourceId)?.name}</strong></div>
              <div className="drawer-route-line" />
              <div><i className="route-point destination-point" /><span>Destination</span><strong>{getLocation(selected.destinationId)?.name}</strong></div>
            </div>
            <div className="drawer-meta-grid">
              <div><PackageOpen size={17} /><span>Package</span><strong>{selected.itemName}</strong><small>{selected.weightKg} kg · {selected.category}</small></div>
              <div><Clock3 size={17} /><span>ETA</span><strong>{selected.etaMinutes ?? "—"} min</strong><small>Updated live</small></div>
              <div><MapPin size={17} /><span>Recipient</span><strong>{selected.recipientName}</strong><small>{selected.recipientPhone}</small></div>
              <div><CalendarDays size={17} /><span>Requested</span><strong>{new Date(selected.createdAt).toLocaleDateString()}</strong><small>{new Date(selected.createdAt).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</small></div>
            </div>
            {selected.unlockCode && <div className="unlock-card"><span>Recipient unlock code</span><strong>{selected.unlockCode}</strong><small>Show only after the robot confirms arrival.</small></div>}
            <div className="drawer-section"><h3>Mission timeline</h3><DeliveryTimeline status={selected.status} /></div>
            {selected.notes && <div className="drawer-section"><h3>Handling notes</h3><p>{selected.notes}</p></div>}
            {["REQUESTED", "APPROVED"].includes(selected.status) && <button className="button button-danger-outline button-full" onClick={() => { void cancelDelivery(selected.id); setSelected(undefined); }}>Cancel request</button>}
          </aside>
        </div>
      )}
    </div>
  );
}
