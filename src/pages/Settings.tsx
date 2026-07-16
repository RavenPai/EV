import { CheckCircle2, Cloud, Code2, Copy, Database, ExternalLink, KeyRound, MessageSquareMore, RefreshCw, Router, Server, ShieldCheck, TerminalSquare } from "lucide-react";
import { useApp } from "../context/AppContext";
import { backendMode, cloudEnabled } from "../lib/supabase";

const environment = [
  ["VITE_SUPABASE_URL", "Public Supabase project URL", false],
  ["VITE_SUPABASE_PUBLISHABLE_KEY", "Browser-safe publishable key", false],
  ["EMQX_API_URL", "Edge Function only", true],
  ["EMQX_API_KEY", "Edge Function secret", true],
  ["EMQX_API_SECRET", "Edge Function secret", true],
];

export function Settings() {
  const { resetDemo } = useApp();
  return (
    <div className="settings-page">
      <section className="integration-hero">
        <div><span className="eyebrow"><Cloud size={14} /> Deployment readiness</span><h2>{cloudEnabled ? "Cloud services are connected" : "Demo mode is ready; connect cloud services next."}</h2><p>The interface works without credentials for demonstrations. Apply the included Supabase migration and set the environment variables to enable persistent multi-user operation.</p></div>
        <div className="integration-score"><span>Readiness</span><strong>{cloudEnabled ? "100" : "72"}<em>%</em></strong><small>{backendMode}</small></div>
      </section>

      <section className="settings-grid">
        <article className="panel architecture-panel">
          <div className="panel-heading"><div><span className="eyebrow">System path</span><h3>Cloud-to-robot architecture</h3></div><Router size={22} className="heading-icon" /></div>
          <div className="architecture-flow">
            <div><span className="architecture-icon"><Code2 size={20} /></span><strong>React web app</strong><small>Cloudflare Pages</small></div><i>HTTPS</i>
            <div><span className="architecture-icon"><Database size={20} /></span><strong>Supabase</strong><small>Auth + PostgreSQL</small></div><i>MQTT/TLS</i>
            <div><span className="architecture-icon"><MessageSquareMore size={20} /></span><strong>EMQX</strong><small>Command broker</small></div><i>TLS</i>
            <div><span className="architecture-icon"><Server size={20} /></span><strong>Raspberry Pi</strong><small>Mission manager</small></div><i>UART</i>
            <div><span className="architecture-icon"><TerminalSquare size={20} /></span><strong>ESP32</strong><small>Motor PID</small></div>
          </div>
          <div className="architecture-rule"><ShieldCheck size={18} /><p><strong>Safety rule:</strong> The Internet assigns missions. The Raspberry Pi and ESP32 retain authority over navigation, stopping and motor output.</p></div>
        </article>

        <article className="panel services-panel">
          <div className="panel-heading"><div><span className="eyebrow">Integration status</span><h3>Required services</h3></div><span className="count-badge">3 services</span></div>
          <div className="service-list">
            <div><span className="service-icon service-supabase"><Database size={20} /></span><div><strong>Supabase</strong><p>Authentication, deliveries, robot state and audit events</p></div><span className={`service-state ${cloudEnabled ? "connected" : "pending"}`}>{cloudEnabled ? "Connected" : "Configure"}</span></div>
            <div><span className="service-icon service-emqx"><MessageSquareMore size={20} /></span><div><strong>EMQX Cloud</strong><p>Durable mission commands and robot acknowledgements</p></div><span className="service-state pending">Edge secret</span></div>
            <div><span className="service-icon service-cloudflare"><Cloud size={20} /></span><div><strong>Cloudflare Pages</strong><p>Global HTTPS hosting for the frontend application</p></div><span className="service-state ready">Ready</span></div>
          </div>
        </article>
      </section>

      <section className="settings-lower-grid">
        <article className="panel environment-panel">
          <div className="panel-heading"><div><span className="eyebrow">Configuration</span><h3>Environment variables</h3></div><KeyRound size={21} className="heading-icon" /></div>
          <div className="environment-list">
            {environment.map(([key, purpose, secret]) => <div key={key as string}><code>{key}</code><span>{purpose}</span><small>{secret ? "Server secret" : "Frontend"}</small><button aria-label={`Copy ${key}`} onClick={() => void navigator.clipboard?.writeText(key as string)}><Copy size={15} /></button></div>)}
          </div>
          <div className="config-note"><CheckCircle2 size={17} /><p>Publishable Supabase credentials may be exposed in the browser because Row Level Security protects the data. EMQX credentials must stay inside Edge Function secrets.</p></div>
        </article>

        <article className="panel setup-steps-panel">
          <div className="panel-heading"><div><span className="eyebrow">Go live</span><h3>Connection checklist</h3></div><ExternalLink size={20} className="heading-icon" /></div>
          <ol className="setup-steps">
            <li><span>1</span><div><strong>Create the Supabase project</strong><p>Run <code>supabase db push</code> with the included migration.</p></div></li>
            <li><span>2</span><div><strong>Deploy the Edge Function</strong><p>Set EMQX secrets, then deploy <code>dispatch-delivery</code>.</p></div></li>
            <li><span>3</span><div><strong>Add frontend variables</strong><p>Copy the project URL and publishable key into the hosting dashboard.</p></div></li>
            <li><span>4</span><div><strong>Register Raspberry Pi credentials</strong><p>Use a unique per-robot MQTT username and restricted topic ACL.</p></div></li>
          </ol>
        </article>

        <article className="panel demo-tools-panel">
          <div className="panel-heading"><div><span className="eyebrow">Development tools</span><h3>Demo data</h3></div><RefreshCw size={20} className="heading-icon" /></div>
          <p>Restore the sample missions, robots and notifications used for UI testing.</p>
          <button className="button button-secondary button-full" onClick={resetDemo}><RefreshCw size={17} />Reset demo workspace</button>
        </article>
      </section>
    </div>
  );
}
