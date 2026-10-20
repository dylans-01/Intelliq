import clsx from "clsx";
import {
  Database,
  FileInput,
  LayoutDashboard,
  LogOut,
  Menu as MenuIcon,
  Monitor,
  Moon,
  PanelLeftClose,
  PanelLeftOpen,
  Search,
  ShieldCheck,
  Sigma,
  Sun,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { useDashboards } from "@/api/hooks";
import { useAuth, type Capability } from "@/auth/AuthProvider";
import { IconButton, Popover, Segmented, Select } from "@/design-system";
import { useTheme, type ThemeChoice } from "@/lib/theme";
import "./shell.css";

interface NavItem {
  to: string;
  label: string;
  icon: ReactNode;
  cap?: Capability;
}

const NAV: NavItem[] = [
  { to: "/dashboards", label: "Dashboards", icon: <LayoutDashboard /> },
  { to: "/metrics", label: "Metrics", icon: <Sigma />, cap: "view_datasets" },
  { to: "/data", label: "Data sources", icon: <Database />, cap: "manage_connections" },
  { to: "/import", label: "Tableau import", icon: <FileInput />, cap: "import_workbooks" },
  { to: "/admin", label: "Administration", icon: <ShieldCheck />, cap: "manage_users" },
];

function readCollapsed(): boolean {
  try {
    return localStorage.getItem("intelliq-sidebar") === "collapsed";
  } catch {
    return false;
  }
}

export function AppShell() {
  const { can } = useAuth();
  const [collapsed, setCollapsed] = useState(readCollapsed);
  const [mobileOpen, setMobileOpen] = useState(false);
  const location = useLocation();
  useEffect(() => setMobileOpen(false), [location.pathname]);
  useEffect(() => {
    try {
      localStorage.setItem("intelliq-sidebar", collapsed ? "collapsed" : "expanded");
    } catch {
      /* storage unavailable */
    }
  }, [collapsed]);

  return (
    <div className={clsx("shell", collapsed && "shell--collapsed", mobileOpen && "shell--mobile-open")}>
      <a className="shell-skip" href="#main">
        Skip to content
      </a>
      <aside className="shell-sidebar" aria-label="Primary">
        <div className="shell-brand">
          <span className="shell-brand__mark" aria-hidden>
            <svg viewBox="0 0 32 32" width="22" height="22">
              <rect width="32" height="32" rx="8" fill="var(--accent)" />
              <path d="M9 22V14M16 22V9M23 22v-5" stroke="var(--accent-ink)" strokeWidth="3" strokeLinecap="round" />
            </svg>
          </span>
          <span className="shell-brand__name">Intelliq</span>
        </div>
        <nav className="shell-nav">
          {NAV.filter((n) => !n.cap || can(n.cap)).map((n) => (
            <NavLink key={n.to} to={n.to} className="shell-nav__item" title={collapsed ? n.label : undefined}>
              {n.icon}
              <span>{n.label}</span>
            </NavLink>
          ))}
        </nav>
        <div className="shell-sidebar__footer">
          <IconButton
            label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
            icon={collapsed ? <PanelLeftOpen /> : <PanelLeftClose />}
            size="sm"
            onClick={() => setCollapsed((c) => !c)}
            className="shell-collapse"
          />
        </div>
      </aside>
      <div className="shell-scrim" onClick={() => setMobileOpen(false)} aria-hidden />
      <div className="shell-main">
        <header className="shell-topbar">
          <IconButton label="Open navigation" icon={<MenuIcon />} className="shell-hamburger" onClick={() => setMobileOpen(true)} />
          <QuickSearch />
          <div className="shell-topbar__right">
            <WorkspaceSwitcher />
            <AccountMenu />
          </div>
        </header>
        <main id="main" className="shell-content" tabIndex={-1}>
          <Outlet />
        </main>
      </div>
    </div>
  );
}

function QuickSearch() {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [active, setActive] = useState(0);
  const anchor = useRef<HTMLButtonElement>(null);
  const navigate = useNavigate();
  const { data } = useDashboards();
  const close = useCallback(() => setOpen(false), []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setOpen(true);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const results = (data ?? []).filter((d) => d.title.toLowerCase().includes(q.trim().toLowerCase())).slice(0, 8);
  const go = (id: string) => {
    close();
    setQ("");
    navigate(`/dashboards/${id}`);
  };

  return (
    <>
      <button ref={anchor} className="shell-search" onClick={() => setOpen(true)} aria-haspopup="dialog">
        <Search aria-hidden />
        <span>Search dashboards</span>
        <kbd className="iq-kbd">⌘K</kbd>
      </button>
      <Popover open={open} onClose={close} anchor={anchor} width={420} label="Search dashboards">
        <div style={{ padding: 4 }}>
          <input
            className="iq-input"
            autoFocus
            placeholder="Type to search…"
            value={q}
            aria-label="Search dashboards"
            onChange={(e) => {
              setQ(e.target.value);
              setActive(0);
            }}
            onKeyDown={(e) => {
              if (e.key === "ArrowDown") setActive((a) => Math.min(a + 1, results.length - 1));
              if (e.key === "ArrowUp") setActive((a) => Math.max(a - 1, 0));
              if (e.key === "Enter" && results[active]) go(results[active].id);
            }}
          />
          <div className="iq-menu-label" style={{ marginTop: 6 }}>
            Dashboards
          </div>
          {results.length === 0 && <div className="iq-state iq-state--compact">No dashboards match</div>}
          {results.map((d, i) => (
            <button key={d.id} className="iq-menu-item" data-active={i === active ? "true" : undefined} onMouseEnter={() => setActive(i)} onClick={() => go(d.id)}>
              <LayoutDashboard />
              <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{d.title}</span>
              {d.status === "draft" && <span className="muted" style={{ fontSize: 11 }}>Draft</span>}
            </button>
          ))}
        </div>
      </Popover>
    </>
  );
}

function WorkspaceSwitcher() {
  const { me, switchWorkspace } = useAuth();
  if (!me || me.workspaces.length < 2) return null;
  return (
    <Select
      size_="sm"
      aria-label="Workspace"
      style={{ width: 180 }}
      value={me.workspace.id}
      onChange={(e) => void switchWorkspace(e.target.value)}
      options={me.workspaces.map((w) => ({ value: w.id, label: w.name }))}
    />
  );
}

function AccountMenu() {
  const { me, logout } = useAuth();
  const { choice, setChoice } = useTheme();
  const [open, setOpen] = useState(false);
  const anchor = useRef<HTMLButtonElement>(null);
  const close = useCallback(() => setOpen(false), []);
  const navigate = useNavigate();
  if (!me) return null;
  const initials = me.user.name
    .split(/\s+/)
    .map((s) => s[0])
    .join("")
    .slice(0, 2)
    .toUpperCase();
  return (
    <>
      <button ref={anchor} className="shell-avatar" aria-label="Account" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        {initials}
      </button>
      <Popover open={open} onClose={close} anchor={anchor} align="end" width={260} label="Account">
        <div style={{ padding: "8px 8px 10px" }}>
          <div style={{ fontWeight: 600, fontSize: 13 }}>{me.user.name}</div>
          <div className="muted" style={{ fontSize: 12 }}>
            {me.user.email} · {me.workspace.role}
          </div>
        </div>
        <div className="iq-menu-sep" />
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "6px 8px" }}>
          <span style={{ fontSize: 13 }}>Theme</span>
          <Segmented<ThemeChoice>
            label="Theme"
            value={choice}
            onChange={setChoice}
            options={[
              { value: "light", icon: <Sun />, title: "Light" },
              { value: "dark", icon: <Moon />, title: "Dark" },
              { value: "system", icon: <Monitor />, title: "System" },
            ]}
          />
        </div>
        <div className="iq-menu-sep" />
        <button
          className="iq-menu-item"
          onClick={async () => {
            close();
            await logout();
            navigate("/login");
          }}
        >
          <LogOut /> Sign out
        </button>
      </Popover>
    </>
  );
}

/** Consistent page header: title, optional description and actions. */
export function PageHeader({ title, description, actions, meta }: { title: ReactNode; description?: ReactNode; actions?: ReactNode; meta?: ReactNode }) {
  return (
    <div className="page-header">
      <div className="page-header__text">
        <h1>{title}</h1>
        {meta && <div className="page-header__meta">{meta}</div>}
        {description && <p className="page-header__desc">{description}</p>}
      </div>
      {actions && <div className="page-header__actions">{actions}</div>}
    </div>
  );
}
