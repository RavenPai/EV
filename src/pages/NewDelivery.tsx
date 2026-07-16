import { useMemo, useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { ArrowRight, CheckCircle2, Info, MapPin, Package, Route, Scale, ShieldCheck, UserRound } from "lucide-react";
import { CampusMap } from "../components/CampusMap";
import { useApp } from "../context/AppContext";
import { getLocation, locations } from "../data/demo";
import type { Delivery, NewDeliveryInput } from "../types";

const categories = ["Documents", "Electronics", "Food", "Books", "Medical supplies", "Other"];

export function NewDelivery() {
  const navigate = useNavigate();
  const { createDelivery } = useApp();
  const [submitting, setSubmitting] = useState(false);
  const [accepted, setAccepted] = useState(false);
  const [error, setError] = useState("");
  const [form, setForm] = useState<NewDeliveryInput>({
    sourceId: "loc-fcs", destinationId: "loc-data", itemName: "", category: "Documents", weightKg: 1,
    priority: "NORMAL", recipientName: "", recipientPhone: "", notes: "",
  });
  const previewDelivery = useMemo<Delivery>(() => ({
    id: "preview", trackingCode: "Route preview", requesterName: "", requesterEmail: "", recipientName: form.recipientName,
    recipientPhone: form.recipientPhone, sourceId: form.sourceId, destinationId: form.destinationId, itemName: form.itemName,
    category: form.category, weightKg: form.weightKg, priority: form.priority, status: "REQUESTED",
    createdAt: "", updatedAt: "", progress: 12, etaMinutes: 18,
  }), [form]);

  const update = <K extends keyof NewDeliveryInput>(key: K, value: NewDeliveryInput[K]) => setForm((current) => ({ ...current, [key]: value }));

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setError("");
    if (form.sourceId === form.destinationId) return setError("Source and destination must be different locations.");
    if (!form.itemName.trim() || !form.recipientName.trim()) return setError("Please enter the item and recipient information.");
    if (form.weightKg <= 0 || form.weightKg > 10) return setError("The prototype payload must be between 0.1 kg and 10 kg.");
    if (!accepted) return setError("Confirm that the package is safe for autonomous delivery.");
    setSubmitting(true);
    try {
      await createDelivery(form);
      navigate("/deliveries");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "The delivery could not be created.");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <form className="new-delivery-layout" onSubmit={submit}>
      <div className="form-column">
        <section className="panel form-section">
          <div className="section-number">1</div>
          <div className="form-section-content">
            <div className="form-section-heading"><div><h2>Choose the route</h2><p>Select only registered campus pickup and delivery points.</p></div><Route size={22} /></div>
            <div className="field-grid two-columns">
              <label><span><MapPin size={15} /> Pickup location</span><select value={form.sourceId} onChange={(event) => update("sourceId", event.target.value)}>{locations.map((location) => <option value={location.id} key={location.id}>{location.name}</option>)}</select><small>{getLocation(form.sourceId)?.description}</small></label>
              <label><span><MapPin size={15} /> Destination</span><select value={form.destinationId} onChange={(event) => update("destinationId", event.target.value)}>{locations.map((location) => <option value={location.id} key={location.id}>{location.name}</option>)}</select><small>{getLocation(form.destinationId)?.description}</small></label>
            </div>
          </div>
        </section>

        <section className="panel form-section">
          <div className="section-number">2</div>
          <div className="form-section-content">
            <div className="form-section-heading"><div><h2>Package information</h2><p>These details help the operator select a safe robot and route.</p></div><Package size={22} /></div>
            <div className="field-grid two-columns">
              <label className="field-span-2"><span>Item description</span><input placeholder="e.g. Signed laboratory documents" value={form.itemName} onChange={(event) => update("itemName", event.target.value)} maxLength={80} /></label>
              <label><span>Category</span><select value={form.category} onChange={(event) => update("category", event.target.value)}>{categories.map((category) => <option key={category}>{category}</option>)}</select></label>
              <label><span><Scale size={15} /> Approximate weight</span><div className="input-suffix"><input type="number" min="0.1" max="10" step="0.1" value={form.weightKg} onChange={(event) => update("weightKg", Number(event.target.value))} /><i>kg</i></div></label>
              <div className="field-span-2"><span className="field-label">Priority</span><div className="segmented-control">{(["NORMAL", "HIGH", "URGENT"] as const).map((priority) => <button type="button" key={priority} className={form.priority === priority ? "active" : ""} onClick={() => update("priority", priority)}>{priority.charAt(0) + priority.slice(1).toLowerCase()}</button>)}</div></div>
              <label className="field-span-2"><span>Handling notes <em>Optional</em></span><textarea rows={3} placeholder="Door, room, deadline or handling instructions" value={form.notes} onChange={(event) => update("notes", event.target.value)} maxLength={240} /></label>
            </div>
          </div>
        </section>

        <section className="panel form-section">
          <div className="section-number">3</div>
          <div className="form-section-content">
            <div className="form-section-heading"><div><h2>Recipient and confirmation</h2><p>The recipient receives the one-time cargo unlock code.</p></div><UserRound size={22} /></div>
            <div className="field-grid two-columns">
              <label><span>Recipient name</span><input placeholder="Full name or office" value={form.recipientName} onChange={(event) => update("recipientName", event.target.value)} /></label>
              <label><span>Phone number</span><input placeholder="+95 9 ..." value={form.recipientPhone} onChange={(event) => update("recipientPhone", event.target.value)} /></label>
              <label className="checkbox-card field-span-2"><input type="checkbox" checked={accepted} onChange={(event) => setAccepted(event.target.checked)} /><span className="custom-checkbox"><CheckCircle2 size={17} /></span><div><strong>Package safety confirmed</strong><p>The package is closed, stable, legal, below 10 kg and contains no hazardous, hot or leaking material.</p></div></label>
            </div>
          </div>
        </section>
        {error && <div className="form-error"><Info size={17} />{error}</div>}
        <div className="form-actions"><button type="button" className="button button-ghost" onClick={() => navigate(-1)}>Cancel</button><button className="button button-primary button-large" disabled={submitting}>{submitting ? "Submitting…" : "Submit delivery request"}<ArrowRight size={18} /></button></div>
      </div>

      <aside className="request-summary-column">
        <section className="panel sticky-summary">
          <div className="panel-heading"><div><span className="eyebrow">Request summary</span><h3>Mission preview</h3></div><ShieldCheck size={21} className="heading-icon" /></div>
          <CampusMap delivery={previewDelivery} className="map-compact" />
          <div className="summary-route"><div><i className="route-point source-point" /><span>Pickup</span><strong>{getLocation(form.sourceId)?.name}</strong></div><ArrowRight size={18} /><div><i className="route-point destination-point" /><span>Destination</span><strong>{getLocation(form.destinationId)?.name}</strong></div></div>
          <div className="summary-facts"><div><span>Estimated distance</span><strong>420 m</strong></div><div><span>Estimated duration</span><strong>16–20 min</strong></div><div><span>Payload</span><strong>{form.weightKg || 0} kg</strong></div><div><span>Priority</span><strong>{form.priority}</strong></div></div>
          <div className="info-callout"><Info size={17} /><p>An administrator must approve and assign this request before the robot receives a mission.</p></div>
        </section>
      </aside>
    </form>
  );
}
