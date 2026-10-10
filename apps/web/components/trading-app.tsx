"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Activity,
  BarChart3,
  Bell,
  BookOpen,
  Bot,
  ChevronRight,
  CircleDollarSign,
  FlaskConical,
  FolderSearch,
  LayoutDashboard,
  ListOrdered,
  LogOut,
  Menu,
  Radio,
  Settings,
  Shield,
  ShieldAlert,
  SquareTerminal,
  Unplug,
  Wallet,
  X,
  Zap,
} from "lucide-react";
import { api, ApiError, setCsrfToken, useApi } from "@/lib/api";
import type { User } from "@/lib/types";
import { Alert, Badge, Button, Dialog, ErrorState, Loading } from "./ui";
import DashboardPage from "./dashboard-page";
import LiveSnapshots from "./live-snapshots";
import StrategiesPage from "./strategies-page";
import BacktestingPage from "./backtesting-page";
import TradingPages from "./trading-pages";
import OperationsPages from "./operations-pages";
const nav = [
  {
    group: "Workspace",
    items: [
      { id: "dashboard", label: "Command center", icon: LayoutDashboard },
      { id: "terminal", label: "Market terminal", icon: SquareTerminal },
      { id: "strategies", label: "Strategies", icon: Bot },
      { id: "backtesting", label: "Backtesting lab", icon: FlaskConical },
    ],
  },
  {
    group: "Trading & research",
    items: [
      { id: "paper", label: "Paper trading", icon: Wallet },
      { id: "demo", label: "Delta India demo", icon: Radio },
      { id: "live", label: "Live readiness", icon: Shield },
      { id: "positions", label: "Positions", icon: CircleDollarSign },
      { id: "orders", label: "Orders", icon: ListOrdered },
      { id: "performance", label: "Performance", icon: BarChart3 },
      { id: "journal", label: "Trade journal", icon: BookOpen },
      { id: "research", label: "Research workspace", icon: FolderSearch },
      { id: "news", label: "News & events", icon: Radio },
      { id: "reports", label: "Reports & analytics", icon: BarChart3 },
    ],
  },
  {
    group: "Operations",
    items: [
      { id: "risk", label: "Risk management", icon: ShieldAlert },
      { id: "notifications", label: "Notifications", icon: Bell },
      { id: "integrations", label: "Integrations", icon: Unplug },
      { id: "system-health", label: "System health", icon: Activity },
      { id: "settings", label: "Settings", icon: Settings },
    ],
  },
];
export default function TradingApp({ section }: { section: string }) {
  const router = useRouter();
  const client = useQueryClient();
  const auth = useApi<User>("/auth/me", 60000);
  const [sidebar, setSidebar] = useState(false);
  const [stopOpen, setStopOpen] = useState(false);
  const current = nav.flatMap((g) => g.items).find((i) => i.id === section);
  useEffect(() => {
    if (auth.data) setCsrfToken(auth.data.csrf_token);
    if (auth.error instanceof ApiError && auth.error.status === 401) {
      setCsrfToken(undefined);
      router.replace("/login");
    }
  }, [auth.data, auth.error, router]);
  const logout = useMutation({
    mutationFn: () => api("/auth/logout", { method: "POST", body: {} }),
    onSuccess: () => {
      setCsrfToken(undefined);
      client.clear();
      router.replace("/login");
    },
  });
  const stop = useMutation({
    mutationFn: () =>
      api("/emergency-stop", {
        method: "POST",
        body: { action: "BLOCK_ENTRIES", confirm: false },
      }),
    onSuccess: () => {
      void client.invalidateQueries();
      setStopOpen(false);
    },
  });
  if (auth.isLoading)
    return (
      <div className="login-page">
        <Loading label="Verifying secure session…" />
      </div>
    );
  if (!auth.data)
    return (
      <div className="login-page">
        <div style={{ maxWidth: 500 }}>
          <ErrorState error={auth.error} retry={() => void auth.refetch()} />
          <Link className="btn btn-primary" href="/login">
            Go to sign in
          </Link>
        </div>
      </div>
    );
  return (
    <div className="app-shell">
      <aside className={`sidebar ${sidebar ? "sidebar-open" : ""}`}>
        <Link href="/" className="brand">
          <div className="brand-mark">
            <BarChart3 size={20} />
          </div>
          <div>
            <div className="brand-name">
              TradeBot <span>Pro</span>
            </div>
            <div className="brand-sub">Delta Exchange India</div>
          </div>
        </Link>
        <nav className="sidebar-nav" aria-label="Main navigation">
          {nav.map((group) => (
            <div key={group.group}>
              <div className="nav-label">{group.group}</div>
              {group.items.map(({ id, label, icon: Icon }) => (
                <Link
                  key={id}
                  className={`nav-item ${section === id ? "active" : ""}`}
                  href={id === "dashboard" ? "/" : `/${id}`}
                  onClick={() => setSidebar(false)}
                  aria-current={section === id ? "page" : undefined}
                >
                  <Icon />
                  {label}
                </Link>
              ))}
            </div>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <div className="environment-box">
            <div className="small-row">
              <span>EXECUTION ENVIRONMENT</span>
              <Badge tone="blue">{section === "demo" ? "DEMO" : "PAPER"}</Badge>
            </div>
            <div className="compact-stat">
              <span
                className={`status-dot ${section === "demo" ? "dot-amber" : "dot-green"}`}
              />
              <strong>
                {section === "demo"
                  ? "Demo account workspace"
                  : "Isolated simulation"}
              </strong>
            </div>
            <p className="sidebar-note">
              {section === "demo"
                ? "Demo orders reach the isolated Delta India demo exchange after explicit authorization."
                : "Exchange data is real. Paper funds, orders and fills are simulated."}
            </p>
          </div>
        </div>
      </aside>
      <div className="workspace">
        <header className="topbar">
          <button
            className="icon-button mobile-menu"
            aria-label={sidebar ? "Close navigation" : "Open navigation"}
            onClick={() => setSidebar(!sidebar)}
          >
            {sidebar ? <X size={20} /> : <Menu size={20} />}
          </button>
          <div className="topbar-crumb">
            Workspace{" "}
            <ChevronRight
              size={11}
              style={{ display: "inline", margin: "0 8px" }}
            />
            <strong>{current?.label ?? "Research"}</strong>
          </div>
          <div className="topbar-right">
            <LiveSnapshots />
            <Badge tone={section === "live" ? "red" : "blue"}>
              {section === "demo"
                ? "DEMO ACCOUNT"
                : section === "live"
                  ? "LIVE DISABLED"
                  : "PAPER MODE"}
            </Badge>
            <div className="divider" />
            <Link
              className="icon-button"
              aria-label="Open notifications"
              href="/notifications"
            >
              <Bell size={17} />
            </Link>
            <div className="user-button">
              <span className="user-avatar">
                {auth.data.username.slice(0, 2).toUpperCase()}
              </span>
              <div>
                <div className="user-name">{auth.data.username}</div>
                <div className="user-role">{auth.data.role}</div>
              </div>
            </div>
            <button
              className="icon-button"
              aria-label="Sign out"
              disabled={logout.isPending}
              onClick={() => logout.mutate()}
            >
              <LogOut size={15} />
            </button>
          </div>
        </header>
        <main className="content">
          {logout.error && (
            <Alert tone="red">Sign out failed: {logout.error.message}</Alert>
          )}
          {section === "dashboard" ? (
            <DashboardPage onEmergency={() => setStopOpen(true)} />
          ) : section === "strategies" ? (
            <StrategiesPage />
          ) : section === "backtesting" ? (
            <BacktestingPage />
          ) : [
              "paper",
              "positions",
              "orders",
              "terminal",
              "performance",
            ].includes(section) ? (
            <TradingPages section={section} />
          ) : (
            <OperationsPages
              section={section}
              onEmergency={() => setStopOpen(true)}
            />
          )}
          <footer className="page-footer">
            <span>TRADEBOT PRO · DELTA EXCHANGE INDIA</span>
            <span>
              <Zap
                size={10}
                style={{ display: "inline", verticalAlign: "middle" }}
              />{" "}
              Research → Backtest → Paper → Verified demo → Authorized live
            </span>
          </footer>
        </main>
      </div>
      <Dialog
        open={stopOpen}
        onOpenChange={setStopOpen}
        title="Block new paper exposure?"
        description="Emergency stop prevents new entries and pauses active strategies. Existing positions remain open until you close them separately."
      >
        {stop.error && <Alert tone="red">{stop.error.message}</Alert>}
        <Alert tone="amber">
          This action does not close positions. Review the positions page to
          manage existing exposure.
        </Alert>
        <div className="form-actions">
          <Button variant="secondary" onClick={() => setStopOpen(false)}>
            Cancel
          </Button>
          <Button
            variant="danger"
            disabled={stop.isPending}
            onClick={() => stop.mutate()}
          >
            <ShieldAlert size={15} />
            {stop.isPending ? "Stopping…" : "Block entries & pause strategies"}
          </Button>
        </div>
      </Dialog>
    </div>
  );
}
