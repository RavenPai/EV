import { useEffect, useMemo, useState, type ReactNode } from "react";
import { NavLink, useLocation, useNavigate } from "react-router-dom";
import {
  Bell, Bot, Boxes, ChevronDown, CircleUserRound, ClipboardList, Command, LayoutDashboard,
  Menu, PackagePlus, Radio, Settings, ShieldCheck, X,
} from "lucide-react";
import { useApp } from "../context/AppContext";
import { backendMode, cloudEnabled, supabase } from "../lib/supabase";
import type { UserRole } from "../types";

const navigation = [
  { to: "/", label: "Overview", icon: LayoutDashboard, roles: ["USER", "ADMIN", "OPERATOR"] },
  { to: "/new-delivery", label: "New delivery", icon: PackagePlus, roles: ["USER", "ADMIN"] },
  { to: "/deliveries", label: "Deliveries", icon: ClipboardList, roles: ["USER", "ADMIN", "OPERATOR"] },
  { to: "/dispatch", label: "Dispatch center", icon: Command, roles: ["ADMIN", "OPERATOR"] },
  { to: "/fleet", label: "Robot fleet", icon: Bot, roles: ["ADMIN", "OPERATOR"] },
  { to: "/settings", label: "System setup", icon: Settings, roles: ["ADMIN", "OPERATOR"] },
] as const;

const pageTitles: Record<string, { title: string; subtitle: string }> = {
  "/": { title: "Operations overview", subtitle: "Campus delivery activity and robot readiness" },
  "/new-delivery": { title: "Request a delivery", subtitle: "Create a safe source-to-destination mission" },
  "/deliveries": { title: "Delivery records", subtitle: "Track requests, active missions and completed deliveries" },
  "/dispatch": { title: "Dispatch center", subtitle: "Approve requests, assign robots and release missions" },
  "/fleet": { title: "Robot fleet", subtitle: "Live health, telemetry and mission controls" },
  "/settings": { title: "System setup", subtitle: "Cloud integration and deployment readiness" },
};

const roleLabels: Record<UserRole, string> = { USER: "Campus user", ADMIN: "Administrator", OPERATOR: "Robot operator" };

export function AppShell({ children }: { children: ReactNode }) {
  const location = useLocation();
  const navigate = useNavigate();
  const { role, setRole, notifications, markNotificationsRead, toast, dismissToast } = useApp();
  const [mobileOpen, setMobileOpen] = useState(false);
  const [roleOpen, setRoleOpen] = useState(false);
  const [notificationsOpen, setNotificationsOpen] = useState(false);
  const unread = notifications.filter((item) => !item.read).length;
  const page = pageTitles[location.pathname] ?? pageTitles["/"];
  const visibleNavigation = useMemo(() => navigation.filter((item) => (item.roles as readonly UserRole[]).includes(role)), [role]);

  useEffect(() => setMobileOpen(false), [location.pathname]);
  useEffect(() => {
    if (role === "USER" && ["/dispatch", "/fleet", "/settings"].includes(location.pathname)) navigate("/", { replace: true });
  }, [role, location.pathname, navigate]);
  useEffect(() => {
    if (!toast) return;
    const timer = window.setTimeout(dismissToast, 4200);
    return () => window.clearTimeout(timer);
  }, [toast, dismissToast]);

  return (
    <div className="app-shell">
      <aside className={`sidebar ${mobileOpen ? "is-open" : ""}`}>
        <div className="brand">
          <div className="brand-mark"><img src="/robot-mark.svg" alt="" /></div>
          <div><strong>MIIT Rover</strong><span>Campus Delivery</span></div>
          <button className="mobile-close" onClick={() => setMobileOpen(false)} aria-label="Close navigation"><X size={20} /></button>
        </div>

        <div className="sidebar-context">
          <span>Workspace</span>
          <strong><Boxes size={15} /> MIIT Campus</strong>
        </div>

        <nav className="main-nav" aria-label="Main navigation">
          <span className="nav-label">Operations</span>
          {visibleNavigation.map(({ to, label, icon: Icon }) => (
            <NavLink key={to} to={to} end={to === "/"} className={({ isActive }) => isActive ? "active" : ""}>
              <Icon size={19} /><span>{label}</span>
            </NavLink>
          ))}
        </nav>

        <div className="sidebar-status">
          <div className="status-orbit"><Radio size={18} /><i /></div>
          <div><span>Backend mode</span><strong>{backendMode}</strong><small>{cloudEnabled ? "Realtime connected" : "Safe demo data"}</small></div>
        </div>
        <div className="sidebar-footer"><ShieldCheck size={16} /><span>Safety commands stay local</span></div>
      </aside>

      {mobileOpen && <button className="sidebar-overlay" onClick={() => setMobileOpen(false)} aria-label="Close menu overlay" />}

      <main className="main-content">
        <header className="topbar">
          <div className="page-title-wrap">
            <button className="menu-button" onClick={() => setMobileOpen(true)} aria-label="Open navigation"><Menu size={21} /></button>
            <div><h1>{page.title}</h1><p>{page.subtitle}</p></div>
          </div>
          <div className="topbar-actions">
            <div className="live-badge"><i /> {cloudEnabled ? "Cloud live" : "Demo live"}</div>
            <div className="popover-wrap">
              <button className="icon-button" onClick={() => { setNotificationsOpen((value) => !value); if (!notificationsOpen) markNotificationsRead(); }} aria-label="Notifications">
                <Bell size={19} />{unread > 0 && <span className="notification-count">{unread}</span>}
              </button>
              {notificationsOpen && (
                <div className="popover notification-popover">
                  <div className="popover-header"><strong>Notifications</strong><span>{notifications.length} recent</span></div>
                  {notifications.slice(0, 4).map((item) => (
                    <div className="notification-item" key={item.id}><i className={`notification-${item.type}`} /><div><strong>{item.title}</strong><p>{item.message}</p><time>{new Date(item.time).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</time></div></div>
                  ))}
                </div>
              )}
            </div>
            <div className="popover-wrap">
              <button className="profile-button" onClick={() => setRoleOpen((value) => !value)}>
                <span className="avatar"><CircleUserRound size={20} /></span>
                <span className="profile-copy"><strong>{roleLabels[role]}</strong><small>Demo session</small></span>
                <ChevronDown size={16} />
              </button>
              {roleOpen && !cloudEnabled && (
                <div className="popover role-popover">
                  <span className="popover-label">View application as</span>
                  {(["USER", "ADMIN", "OPERATOR"] as UserRole[]).map((value) => (
                    <button key={value} className={role === value ? "selected" : ""} onClick={() => { setRole(value); setRoleOpen(false); }}>
                      <span>{roleLabels[value]}</span>{role === value && <ShieldCheck size={16} />}
                    </button>
                  ))}
                </div>
              )}
              {roleOpen && cloudEnabled && (
                <div className="popover role-popover">
                  <span className="popover-label">Signed in role</span>
                  <div className="cloud-role-row"><span>{roleLabels[role]}</span><ShieldCheck size={16} /></div>
                  <button className="signout-button" onClick={() => void supabase?.auth.signOut()}>Sign out</button>
                </div>
              )}
            </div>
          </div>
        </header>
        <div className="page-content">{children}</div>
      </main>

      {toast && <div className={`toast toast-${toast.tone}`} role="status"><span>{toast.message}</span><button onClick={dismissToast}><X size={16} /></button></div>}
    </div>
  );
}
