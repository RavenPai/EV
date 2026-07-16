import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import type { Session } from "@supabase/supabase-js";
import { ArrowRight, CheckCircle2, Cloud, LockKeyhole, Mail, ShieldCheck, UserRound } from "lucide-react";
import { cloudEnabled, supabase } from "../lib/supabase";

export function CloudAuthGate({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(null);
  const [loading, setLoading] = useState(cloudEnabled);
  const [signUp, setSignUp] = useState(false);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [fullName, setFullName] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [working, setWorking] = useState(false);

  useEffect(() => {
    if (!cloudEnabled || !supabase) return;
    const client = supabase;
    void client.auth.getSession().then(({ data }) => {
      setSession(data.session);
      setLoading(false);
    });
    const { data } = client.auth.onAuthStateChange((_event, nextSession) => {
      setSession(nextSession);
      setLoading(false);
    });
    return () => data.subscription.unsubscribe();
  }, []);

  if (!cloudEnabled) return <>{children}</>;
  if (loading) return <div className="auth-loading"><img src="/robot-mark.svg" alt="" /><span>Connecting securely…</span></div>;
  if (session) return <>{children}</>;

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!supabase) return;
    setWorking(true); setError(""); setMessage("");
    try {
      if (signUp) {
        const { data, error: authError } = await supabase.auth.signUp({ email, password, options: { data: { full_name: fullName } } });
        if (authError) throw authError;
        if (!data.session) setMessage("Account created. Check your email to confirm the account, then sign in.");
      } else {
        const { error: authError } = await supabase.auth.signInWithPassword({ email, password });
        if (authError) throw authError;
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Authentication failed.");
    } finally {
      setWorking(false);
    }
  };

  return (
    <div className="auth-page">
      <div className="auth-brand-panel">
        <div className="auth-brand"><img src="/robot-mark.svg" alt="" /><div><strong>MIIT Rover</strong><span>Campus Delivery</span></div></div>
        <div className="auth-hero-copy"><span className="eyebrow"><Cloud size={14} /> Cloud-connected logistics</span><h1>Safe campus delivery, coordinated from one place.</h1><p>Request, dispatch and monitor autonomous EV deliveries while local robot controllers retain authority over movement and stopping.</p></div>
        <div className="auth-benefits"><div><CheckCircle2 size={17} /><span>Role-based delivery workflow</span></div><div><CheckCircle2 size={17} /><span>Live robot status and mission events</span></div><div><CheckCircle2 size={17} /><span>Auditable, expiring command delivery</span></div></div>
        <small>MIIT Campus Logistics · Version 1.0</small>
      </div>
      <div className="auth-form-panel">
        <form className="auth-card" onSubmit={submit}>
          <div className="auth-card-icon"><ShieldCheck size={24} /></div>
          <span className="eyebrow">Secure workspace</span><h2>{signUp ? "Create your account" : "Welcome back"}</h2><p>{signUp ? "Campus users begin with the USER role. An administrator can grant staff access." : "Sign in with your MIIT delivery account."}</p>
          {signUp && <label><span><UserRound size={15} /> Full name</span><input value={fullName} onChange={(event) => setFullName(event.target.value)} required placeholder="Your full name" /></label>}
          <label><span><Mail size={15} /> Email address</span><input type="email" value={email} onChange={(event) => setEmail(event.target.value)} required placeholder="name@miit.edu.mm" /></label>
          <label><span><LockKeyhole size={15} /> Password</span><input type="password" value={password} onChange={(event) => setPassword(event.target.value)} required minLength={8} placeholder="At least 8 characters" /></label>
          {error && <div className="auth-message auth-error">{error}</div>}{message && <div className="auth-message auth-success">{message}</div>}
          <button className="button button-primary button-large button-full" disabled={working}>{working ? "Please wait…" : signUp ? "Create account" : "Sign in"}<ArrowRight size={18} /></button>
          <button type="button" className="auth-toggle" onClick={() => { setSignUp((value) => !value); setError(""); setMessage(""); }}>{signUp ? "Already have an account? Sign in" : "Need a campus account? Create one"}</button>
        </form>
      </div>
    </div>
  );
}
