"use client";
import { useState } from "react";
import DemoPage from "./demo-page";
import ResearchWorkspace from "./research-page";
import NewsPage from "./news-page";
import ReportsPage from "./reports-page";
import SecurityPanel from "./security-panel";
import TelegramPanel from "./telegram-panel";
import LiveReadinessPage from "./live-readiness-page";
import MonitoringPanel from "./monitoring-panel";
import RiskEvidencePanel from "./risk-evidence-panel";
import Link from "next/link";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Check,
  Database,
  FileText,
  FlaskConical,
  KeyRound,
  Lock,
  Pause,
  RefreshCw,
  Save,
  ShieldAlert,
  ShieldCheck,
  Wifi,
  XCircle,
} from "lucide-react";
import { api, asArray, asRecord, format, useApi } from "@/lib/api";
import type { JournalEntry, Notification, RiskConfig } from "@/lib/types";
import {
  Alert,
  Badge,
  Button,
  Card,
  DataTable,
  Dialog,
  Empty,
  ErrorState,
  Field,
  Freshness,
  Input,
  JsonView,
  Loading,
  PageTitle,
  Status,
  Textarea,
} from "./ui";
export default function OperationsPages({
  section,
  onEmergency,
}: {
  section: string;
  onEmergency: () => void;
}) {
  if (section === "risk") return <RiskPage onEmergency={onEmergency} />;
  if (section === "journal") return <JournalPage />;
  if (section === "notifications") return <NotificationsPage />;
  if (section === "integrations") return <IntegrationsPage />;
  if (section === "system-health") return <HealthPage />;
  if (section === "settings") return <SettingsPage />;
  if (section === "demo") return <DemoPage />;
  if (section === "live") return <LiveReadinessPage />;
  if (section === "news") return <NewsPage />;
  if (section === "reports") return <ReportsPage />;
  return <ResearchWorkspace />;
}
function RiskPage({ onEmergency }: { onEmergency: () => void }) {
  const query = useApi<RiskConfig>("/risk", 15000);
  const [command, setCommand] = useState<
    "RESUME_ENTRIES" | "FLATTEN_MANAGED" | null
  >(null);
  const client = useQueryClient();
  const action = useMutation({
    mutationFn: () =>
      api("/emergency-stop", {
        method: "POST",
        body: { action: command, confirm: true },
      }),
    onSuccess: () => {
      setCommand(null);
      void client.invalidateQueries();
    },
  });
  return (
    <>
      <PageTitle
        eyebrow="INDEPENDENT PORTFOLIO CONTROLS"
        title="Paper risk management"
        description="Risk checks live in the backend and apply to every paper strategy. Configuration updates are recorded in the audit journal."
        action={
          <Button variant="danger" onClick={onEmergency}>
            <ShieldAlert size={14} />
            Emergency stop
          </Button>
        }
      />
      {query.isLoading ? (
        <Loading />
      ) : query.error || !query.data ? (
        <ErrorState error={query.error} />
      ) : (
        <>
          <div className="grid-3">
            <Card title="New entry gate">
              <div className="panel-body">
                <Status
                  value={
                    query.data.entries_enabled && !query.data.emergency_stop
                      ? "ENABLED"
                      : "BLOCKED"
                  }
                />
                <p className="list-note">
                  {query.data.entries_enabled && !query.data.emergency_stop
                    ? "New paper exposure is eligible for additional backend checks."
                    : "New exposure is blocked. Existing positions remain managed."}
                </p>
              </div>
            </Card>
            <Card title="Account reconciliation">
              <div className="panel-body">
                <Status
                  value={
                    query.data.reconciliation_required
                      ? "REQUIRED"
                      : "CONSISTENT"
                  }
                />
                <p className="list-note">
                  Uncertain position or order state blocks new exposure until
                  reconciliation succeeds.
                </p>
              </div>
            </Card>
            <Card title="Emergency state">
              <div className="panel-body">
                <Status
                  value={query.data.emergency_stop ? "ACTIVE" : "INACTIVE"}
                />
                <p className="list-note">
                  Pausing entries and flattening positions are distinct actions.
                </p>
              </div>
            </Card>
          </div>
          <div className="grid-2 space-top">
            <Card
              title="Portfolio limits"
              subtitle="Limits apply to simulated settlement equity; percentages below are human readable"
            >
              <div className="panel-body">
                <RiskForm key={JSON.stringify(query.data)} risk={query.data} />
              </div>
            </Card>
            <Card
              title="Emergency actions"
              subtitle="Paper account only; each action is independently enforced"
            >
              <div className="panel-body">
                <Alert tone="amber">
                  A strategy stop does not close positions. Flatten requires
                  fresh market data and may be rejected when safe execution
                  cannot be established.
                </Alert>
                <div className="stack">
                  <div>
                    <h3>Block new entries</h3>
                    <p className="list-note">
                      Pause running strategies, block new exposure and keep
                      existing protection active.
                    </p>
                    <Button variant="danger" onClick={onEmergency}>
                      <Pause size={13} />
                      Block entries & pause
                    </Button>
                  </div>
                  <div>
                    <h3>Resume entry eligibility</h3>
                    <p className="list-note">
                      Reconcile the account before lifting the emergency entry
                      block. Strategies require separate restart.
                    </p>
                    <Button
                      variant="secondary"
                      onClick={() => setCommand("RESUME_ENTRIES")}
                    >
                      <ShieldCheck size={13} />
                      Review resume
                    </Button>
                  </div>
                  <div>
                    <h3>Flatten managed paper positions</h3>
                    <p className="list-note">
                      Close all managed paper positions using fresh prices,
                      cancel paper protection, and keep entries blocked.
                    </p>
                    <Button
                      variant="danger"
                      onClick={() => setCommand("FLATTEN_MANAGED")}
                    >
                      <XCircle size={13} />
                      Review flatten
                    </Button>
                  </div>
                </div>
              </div>
            </Card>
          </div>
          <div style={{ marginTop: 16 }}>
            <Freshness
              updatedAt={query.dataUpdatedAt}
              fetching={query.isFetching}
            />
          </div>
          <RiskEvidencePanel />
        </>
      )}
      <Dialog
        open={command !== null}
        onOpenChange={(open) => !open && setCommand(null)}
        title={
          command === "FLATTEN_MANAGED"
            ? "Flatten all managed paper positions?"
            : "Resume paper entry eligibility?"
        }
        description={
          command === "FLATTEN_MANAGED"
            ? "This closes managed simulated positions. Fresh prices are required and simulated fills include execution costs."
            : "This removes the emergency entry block after backend reconciliation. Strategies stay paused until separately started."
        }
      >
        {action.error && <Alert tone="red">{action.error.message}</Alert>}
        <Alert tone="amber">
          {command === "FLATTEN_MANAGED"
            ? "Existing paper exposure will be closed. No demo or production exchange orders are submitted."
            : "Risk limits and freshness checks remain enforced independently. Review system health before restarting a strategy."}
        </Alert>
        <div className="form-actions">
          <Button variant="secondary" onClick={() => setCommand(null)}>
            Cancel
          </Button>
          <Button
            variant={command === "FLATTEN_MANAGED" ? "danger" : "primary"}
            disabled={action.isPending}
            onClick={() => action.mutate()}
          >
            {action.isPending ? "Checking account…" : "Confirm paper action"}
          </Button>
        </div>
      </Dialog>
    </>
  );
}
function RiskForm({ risk }: { risk: RiskConfig }) {
  const [values, setValues] = useState({
    max_risk_per_trade: Number(risk.max_risk_per_trade) * 100,
    max_total_risk: Number(risk.max_total_risk) * 100,
    max_daily_loss: Number(risk.max_daily_loss) * 100,
    max_weekly_loss: Number(risk.max_weekly_loss) * 100,
    max_pending_orders: risk.max_pending_orders,
    max_margin_fraction: Number(risk.max_margin_fraction) * 100,
    max_gross_exposure_multiple: Number(risk.max_gross_exposure_multiple),
    max_net_exposure_multiple: Number(risk.max_net_exposure_multiple),
    max_spread_bps: Number(risk.max_spread_bps),
    max_slippage_bps: Number(risk.max_slippage_bps),
    max_drawdown: Number(risk.max_drawdown) * 100,
    max_leverage: Number(risk.max_leverage),
    max_open_positions: risk.max_open_positions,
    stale_after_seconds: risk.stale_after_seconds,
    entries_enabled: risk.entries_enabled,
  });
  const client = useQueryClient();
  const [saved, setSaved] = useState(false);
  const [review, setReview] = useState(false);
  const [correlatedCap, setCorrelatedCap] = useState(
    risk.max_correlated_exposure_multiple ?? "",
  );
  const [correlationGroups, setCorrelationGroups] = useState(
    JSON.stringify(risk.correlation_groups ?? [], null, 2),
  );
  const [reviewPayload, setReviewPayload] = useState<Record<
    string,
    unknown
  > | null>(null);
  const [inputError, setInputError] = useState("");
  const payload = {
    ...values,
    max_risk_per_trade: String(values.max_risk_per_trade / 100),
    max_total_risk: String(values.max_total_risk / 100),
    max_daily_loss: String(values.max_daily_loss / 100),
    max_weekly_loss: String(values.max_weekly_loss / 100),
    max_margin_fraction: String(values.max_margin_fraction / 100),
    max_gross_exposure_multiple: String(values.max_gross_exposure_multiple),
    max_net_exposure_multiple: String(values.max_net_exposure_multiple),
    max_spread_bps: String(values.max_spread_bps),
    max_slippage_bps: String(values.max_slippage_bps),
    max_drawdown: String(values.max_drawdown / 100),
    max_leverage: String(values.max_leverage),
  };
  const save = useMutation({
    mutationFn: () => api("/risk", { method: "PUT", body: reviewPayload }),
    onSuccess: () => {
      setSaved(true);
      setReview(false);
      void client.invalidateQueries({ queryKey: ["/risk"] });
      void client.invalidateQueries({ queryKey: ["/journal"] });
    },
  });
  const fields: [keyof typeof values, string, number, number, number][] = [
    ["max_risk_per_trade", "Max risk per trade (%)", 0.1, 5, 0.1],
    ["max_total_risk", "Total portfolio risk (%)", 0.1, 20, 0.1],
    ["max_daily_loss", "Daily loss limit (%)", 0.1, 25, 0.1],
    ["max_weekly_loss", "Weekly loss limit (%)", 0.1, 50, 0.1],
    ["max_pending_orders", "Maximum pending entries", 1, 100, 1],
    [
      "max_margin_fraction",
      "Maximum equity committed as margin (%)",
      1,
      100,
      1,
    ],
    [
      "max_gross_exposure_multiple",
      "Gross exposure / equity limit",
      0.1,
      10,
      0.1,
    ],
    ["max_net_exposure_multiple", "Net exposure / equity limit", 0.1, 10, 0.1],
    ["max_spread_bps", "Maximum spread (basis points)", 0, 100, 0.1],
    [
      "max_slippage_bps",
      "Maximum modeled slippage (basis points)",
      0,
      100,
      0.1,
    ],
    ["max_drawdown", "Maximum drawdown (%)", 0.1, 50, 0.1],
    ["max_leverage", "Maximum leverage", 1, 5, 0.1],
    ["max_open_positions", "Maximum open positions", 1, 10, 1],
    ["stale_after_seconds", "Stale price threshold (seconds)", 30, 300, 1],
  ];
  return (
    <>
      {inputError && <Alert tone="red">{inputError}</Alert>}
      {save.error && <Alert tone="red">{save.error.message}</Alert>}
      {saved && <Alert tone="green">Risk limits saved and audited.</Alert>}
      <form
        onSubmit={(e) => {
          e.preventDefault();
          try {
            const groups: unknown = JSON.parse(correlationGroups);
            if (
              !Array.isArray(groups) ||
              groups.some(
                (group) =>
                  !Array.isArray(group) ||
                  group.length < 2 ||
                  group.some(
                    (symbol) =>
                      typeof symbol !== "string" ||
                      !/^[A-Z0-9]{3,32}$/.test(symbol),
                  ),
              )
            )
              throw new Error(
                "Correlation groups must be an array of symbol arrays, with at least two uppercase instrument symbols in each group.",
              );
            if (Boolean(groups.length) !== Boolean(correlatedCap))
              throw new Error(
                "Set both a group exposure cap and symbol groups, or clear both to disable this limit.",
              );
            setInputError("");
            setReviewPayload({
              ...payload,
              max_correlated_exposure_multiple: correlatedCap || null,
              correlation_groups: groups,
            });
            setReview(true);
          } catch (error) {
            setInputError(
              error instanceof Error
                ? error.message
                : "Invalid correlation groups",
            );
          }
        }}
      >
        <div className="form-grid">
          {fields.map(([key, label, min, max, step]) => (
            <Field key={key} label={label}>
              <Input
                type="number"
                min={min}
                max={max}
                step={step}
                value={Number(values[key])}
                onChange={(e) =>
                  setValues({ ...values, [key]: Number(e.target.value) })
                }
                required
              />
            </Field>
          ))}
        </div>
        <div className="form-section">Declared correlated exposure</div>
        <Field
          label="Correlated group exposure / equity cap"
          hint="Optional conservative cap. Clear both this value and groups to disable."
        >
          <Input
            type="number"
            min="0.1"
            max="10"
            step="0.1"
            value={correlatedCap}
            onChange={(event) => setCorrelatedCap(event.target.value)}
          />
        </Field>
        <Field
          label="Operator-declared correlation groups"
          hint="These are explicit conservative classifications, not automatically inferred historical correlations."
        >
          <Textarea
            className="code-input"
            rows={5}
            value={correlationGroups}
            onChange={(event) => setCorrelationGroups(event.target.value)}
            placeholder='[["BTCUSD", "ETHUSD"]]'
          />
        </Field>
        <p className="list-note">
          Use the explicit resume action to remove an emergency block. Saving
          risk limits preserves the current entry gate.
        </p>
        <div className="form-actions">
          <Button disabled={save.isPending}>
            <Save size={14} />
            Review & save limits
          </Button>
        </div>
      </form>
      <Dialog
        open={review}
        onOpenChange={setReview}
        title="Apply these portfolio limits?"
        description="The backend validates limits and appends the old and new configuration to the audit journal."
      >
        <JsonView value={reviewPayload} />
        <div className="form-actions">
          <Button variant="secondary" onClick={() => setReview(false)}>
            Cancel
          </Button>
          <Button disabled={save.isPending} onClick={() => save.mutate()}>
            {save.isPending ? "Saving…" : "Apply risk limits"}
          </Button>
        </div>
      </Dialog>
    </>
  );
}
function JournalPage() {
  const query = useApi<JournalEntry[]>("/journal", 15000);
  const [search, setSearch] = useState("");
  const [detail, setDetail] = useState<JournalEntry | null>(null);
  const data = (query.data ?? []).filter((e) =>
    `${e.action} ${e.actor} ${e.entity}`
      .toLowerCase()
      .includes(search.toLowerCase()),
  );
  return (
    <>
      <PageTitle
        eyebrow="TRACEABLE OPERATOR ACTIONS"
        title="Trade journal"
        description="An append only audit trail of strategy, backtest, account and risk operations. Trading fills remain available in order and position records."
      />
      <div className="toolbar">
        <Input
          aria-label="Search audit records"
          placeholder="Search action, actor or entity…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <div className="spacer" />
        <Freshness
          updatedAt={query.dataUpdatedAt}
          fetching={query.isFetching}
        />
      </div>
      <Card title="Audit records" subtitle="Most recent 500 persisted records">
        {query.isLoading ? (
          <Loading />
        ) : query.error ? (
          <div className="panel-body">
            <ErrorState error={query.error} />
          </div>
        ) : (
          <DataTable<JournalEntry>
            rows={data}
            getKey={(e) => e.id}
            empty="No audit events match this view"
            columns={[
              { label: "Time (IST)", render: (e) => format.date(e.created_at) },
              {
                label: "Action",
                render: (e) => (
                  <Badge tone="blue">{e.action?.replaceAll("_", " ")}</Badge>
                ),
              },
              { label: "Actor", render: (e) => e.actor },
              {
                label: "Entity",
                render: (e) => <span className="mono">{e.entity}</span>,
              },
              {
                label: "Details",
                render: (e) => (
                  <Button
                    variant="ghost"
                    className="btn-small"
                    onClick={() => setDetail(e)}
                  >
                    <FileText size={12} />
                    Inspect
                  </Button>
                ),
              },
            ]}
          />
        )}
      </Card>
      <Dialog
        open={!!detail}
        onOpenChange={(open) => !open && setDetail(null)}
        title="Audit event"
        description="Immutable persisted operator activity."
      >
        <JsonView value={detail} />
      </Dialog>
    </>
  );
}
function NotificationsPage() {
  const query = useApi<Notification[]>("/notifications", 10000);
  const client = useQueryClient();
  const [unreadOnly, setUnreadOnly] = useState(false);
  const read = useMutation({
    mutationFn: (id: number) =>
      api(`/notifications/${id}/read`, { method: "POST", body: {} }),
    onSuccess: () =>
      void client.invalidateQueries({ queryKey: ["/notifications"] }),
  });
  const rows = (query.data ?? []).filter((n) => !unreadOnly || !n.read);
  return (
    <>
      <PageTitle
        eyebrow="EVENTS & ALERTS"
        title="Notification center"
        description="Persisted in-app risk, worker and execution notifications. External Telegram delivery is separately configured and reported."
        action={
          <Badge tone="blue">
            {(query.data ?? []).filter((n) => !n.read).length} UNREAD
          </Badge>
        }
      />
      {read.error && <Alert tone="red">{read.error.message}</Alert>}
      <div className="toolbar">
        <Button variant="secondary" onClick={() => setUnreadOnly(!unreadOnly)}>
          {unreadOnly ? "Show all" : "Show unread only"}
        </Button>
        <div className="spacer" />
        <Freshness
          updatedAt={query.dataUpdatedAt}
          fetching={query.isFetching}
        />
      </div>
      <Card title="Notifications" subtitle="Latest 300 persisted events">
        {query.isLoading ? (
          <Loading />
        ) : query.error ? (
          <div className="panel-body">
            <ErrorState error={query.error} />
          </div>
        ) : !rows.length ? (
          <Empty
            title="You are all caught up"
            description="Worker, strategy and risk events will appear here as they occur."
          />
        ) : (
          <div className="notice-list">
            {rows.map((n) => (
              <article key={n.id} className="notice-row">
                <div>
                  <Badge
                    tone={
                      n.severity === "CRITICAL" || n.severity === "ERROR"
                        ? "red"
                        : n.severity === "WARNING"
                          ? "amber"
                          : "blue"
                    }
                  >
                    {n.severity}
                  </Badge>
                </div>
                <div className="notice-main">
                  <h3>
                    {n.title ??
                      n.severity
                        .toLowerCase()
                        .replace(/^./, (v) => v.toUpperCase()) + " event"}
                  </h3>
                  <p>{n.message}</p>
                  <div className="notice-time">
                    {format.date(n.created_at)} IST{" "}
                    {n.read ? "· Read" : "· Unread"}
                  </div>
                </div>
                {!n.read && (
                  <Button
                    variant="ghost"
                    className="btn-small"
                    disabled={read.isPending}
                    onClick={() => read.mutate(n.id)}
                    aria-label="Mark notification as read"
                  >
                    <Check size={14} />
                  </Button>
                )}
              </article>
            ))}
          </div>
        )}
      </Card>
    </>
  );
}
function IntegrationsPage() {
  const query = useApi<Record<string, unknown>>("/integrations", 30000);
  const d = query.data;
  return (
    <>
      <PageTitle
        eyebrow="ENVIRONMENT ISOLATION"
        title="Integrations"
        description="Credential presence and verified capability state. Exchange secrets remain on the backend and are never sent to your browser."
        action={<Badge tone="blue">DELTA INDIA ONLY</Badge>}
      />
      {query.isLoading ? (
        <Loading />
      ) : query.error || !d ? (
        <ErrorState error={query.error} />
      ) : (
        <>
          <div className="grid-3">
            <IntegrationCard
              title="Delta India public data"
              subtitle="Production public API; no account credentials required"
              status={String(d.public_data)}
              present={true}
              detail="Used for product discovery and historical candles. This connection does not grant trading permissions."
            />
            {["demo", "live"].map((key) => {
              const integration = asRecord(d[key]);
              return (
                <IntegrationCard
                  key={key}
                  title={
                    key === "demo"
                      ? "Delta India demo"
                      : "Delta India production"
                  }
                  subtitle={
                    key === "demo"
                      ? "Isolated demo/testnet credentials"
                      : "Production account credentials"
                  }
                  status={String(integration.status)}
                  present={integration.credentials_present === true}
                  detail={
                    key === "demo"
                      ? "Dedicated demo execution is available after credentialed connection, verified contract evidence, MFA and independent risk authorization."
                      : "Live execution remains disabled. Credential presence does not authorize order submission."
                  }
                />
              );
            })}
          </div>
          <div className="grid-2 space-top">
            <Card
              title="Telegram notifications"
              subtitle="External alert delivery"
            >
              <div className="panel-body">
                <Status value={String(asRecord(d.telegram).status)} />
                <div className="health-row">
                  <span>Credentials present</span>
                  <strong>
                    {asRecord(d.telegram).credentials_present ? "Yes" : "No"}
                  </strong>
                </div>
                <p className="list-note">
                  In-app notifications and the durable Telegram outbox work
                  independently. External delivery requires separate token and
                  destination configuration.
                </p>
              </div>
            </Card>
            <Card
              title="Configure credentials securely"
              subtitle="Use environment secrets on your own server"
            >
              <div className="panel-body">
                <Alert tone="blue">
                  Never paste exchange secrets into chat or store them in
                  frontend configuration. Configure credentials using secured
                  environment settings or your local protected environment file.
                </Alert>
                <div className="password-info">
                  <code>DELTA_DEMO_API_KEY</code>
                  <br />
                  <code>DELTA_DEMO_API_SECRET</code>
                  <br />
                  <code>DELTA_PRODUCTION_API_KEY</code>
                  <br />
                  <code>DELTA_PRODUCTION_API_SECRET</code>
                  <br />
                  <code>TELEGRAM_BOT_TOKEN</code> and{" "}
                  <code>TELEGRAM_CHAT_ID</code>
                </div>
                <p className="list-note">
                  Production and demo keys are separate bindings. All private
                  account features require integration verification and
                  environment specific authorization.
                </p>
              </div>
            </Card>
          </div>
          <TelegramPanel />
        </>
      )}
    </>
  );
}
function IntegrationCard({
  title,
  subtitle,
  status,
  present,
  detail,
}: {
  title: string;
  subtitle: string;
  status: string;
  present: boolean;
  detail: string;
}) {
  return (
    <Card title={title} subtitle={subtitle}>
      <div className="panel-body">
        <div className="health-row">
          <span>Capability status</span>
          <Status value={status} />
        </div>
        <div className="health-row">
          <span className="health-label">
            <KeyRound />
            Credentials
          </span>
          <Badge tone={present ? "blue" : "neutral"}>
            {present ? "PRESENT / PUBLIC" : "NOT CONFIGURED"}
          </Badge>
        </div>
        <p className="list-note">{detail}</p>
      </div>
    </Card>
  );
}
function HealthPage() {
  const query = useApi<Record<string, unknown>>("/system-health", 10000);
  const d = query.data ?? {},
    worker = asRecord(d.worker),
    workers = asArray<Record<string, unknown>>(worker.workers);
  return (
    <>
      <PageTitle
        eyebrow="MONITORING & RECOVERY"
        title="System health"
        description="Live backend dependency checks, worker heartbeats and trading readiness. Degraded dependencies remain visible."
        action={
          <Button
            variant="secondary"
            onClick={() => void query.refetch()}
            disabled={query.isFetching}
          >
            <RefreshCw size={14} />
            Refresh health
          </Button>
        }
      />
      {query.isLoading ? (
        <Loading />
      ) : query.error ? (
        <ErrorState error={query.error} />
      ) : (
        <>
          <Alert tone={d.status === "healthy" ? "green" : "amber"}>
            <strong>System {String(d.status ?? "unknown")}</strong>
            <p>
              Trading readiness:{" "}
              {d.trading_ready === true ? "reported ready" : "not established"}.
              Paper infrastructure status does not establish demo or live
              readiness.
            </p>
          </Alert>
          <div className="grid-3">
            {[
              ["Database", d.database, Database],
              ["Redis", d.redis, Wifi],
              ["Trading worker", worker.status, FlaskConical],
            ].map(([title, status, Icon]) => {
              const Component = Icon as typeof Database;
              return (
                <Card key={String(title)} title={String(title)}>
                  <div className="panel-body">
                    <Component
                      size={24}
                      className="text-muted"
                      style={{ marginBottom: 13 }}
                    />
                    <Status
                      value={String(
                        status === "ok" ? "HEALTHY" : (status ?? "UNKNOWN"),
                      )}
                    />
                  </div>
                </Card>
              );
            })}
          </div>
          <Card
            className="space-top"
            title="Worker heartbeats"
            subtitle="Actual durable backend heartbeats; stale workers are not trading ready"
            action={
              <Freshness
                updatedAt={query.dataUpdatedAt}
                fetching={query.isFetching}
              />
            }
          >
            <DataTable
              rows={workers}
              getKey={(w, i) => String(w.id ?? i)}
              empty="No worker heartbeat has been recorded"
              columns={[
                {
                  label: "Worker",
                  render: (w) =>
                    String(w.name ?? w.id ?? w.worker_id ?? "worker"),
                },
                {
                  label: "Status",
                  render: (w) => (
                    <Status
                      value={String(
                        w.stale ? "STALE" : (w.status ?? "UNKNOWN"),
                      )}
                    />
                  ),
                },
                {
                  label: "Heartbeat",
                  render: (w) => format.date(w.heartbeat_at ?? w.updated_at),
                },
                {
                  label: "Details",
                  render: (w) => String(w.last_error ?? w.message ?? "—"),
                },
              ]}
            />
          </Card>
          <Card className="space-top" title="Full health evidence">
            <div className="panel-body">
              <JsonView value={d} />
            </div>
          </Card>
          <MonitoringPanel />
        </>
      )}
    </>
  );
}
function SettingsPage() {
  const query = useApi<Record<string, unknown>>("/settings", 30000);
  const auth = useApi<Record<string, unknown>>("/auth/me", 60000);
  return (
    <>
      <PageTitle
        eyebrow="ACCOUNT & WORKSPACE"
        title="Settings"
        description="Persisted planning inputs and secure operator session information. Execution permission is controlled separately."
      />
      {query.isLoading ? (
        <Loading />
      ) : query.error || !query.data ? (
        <ErrorState error={query.error} />
      ) : (
        <>
          <div className="grid-2">
            <Card
              title="Capital planning"
              subtitle="Planning targets never override portfolio risk"
            >
              <div className="panel-body">
                <PlanningForm
                  key={JSON.stringify(query.data)}
                  settings={query.data}
                />
              </div>
            </Card>
            <Card
              title="Security & execution"
              subtitle="Current session and environment"
            >
              <div className="panel-body">
                <div className="health-row">
                  <span>Operator</span>
                  <strong>{String(auth.data?.username ?? "—")}</strong>
                </div>
                <div className="health-row">
                  <span>Role</span>
                  <Badge>{String(auth.data?.role ?? "—")}</Badge>
                </div>
                <div className="health-row">
                  <span>Execution environment</span>
                  <Badge tone="blue">PAPER</Badge>
                </div>
                <div className="health-row">
                  <span>Live execution</span>
                  <Badge tone="red">DISABLED</Badge>
                </div>
                <div className="health-row">
                  <span>Exchange</span>
                  <strong>Delta Exchange India</strong>
                </div>
                <p className="list-note">
                  HttpOnly sessions protect authentication tokens. Writes use
                  CSRF protection and backend role checks. MFA enrollment,
                  session revocation and user administration are available
                  below. Sensitive actions require recent verification.
                </p>
                <Link className="btn btn-secondary" href="/live">
                  <Lock size={13} />
                  Review live readiness
                </Link>
              </div>
            </Card>
          </div>
          <SecurityPanel />
        </>
      )}
    </>
  );
}
function PlanningForm({ settings }: { settings: Record<string, unknown> }) {
  const [capital, setCapital] = useState(
    String(settings.planning_capital_inr ?? "50000"),
  );
  const [rate, setRate] = useState(
    settings.usd_inr_rate ? String(settings.usd_inr_rate) : "",
  );
  const [saved, setSaved] = useState(false);
  const client = useQueryClient();
  const save = useMutation({
    mutationFn: () =>
      api("/settings", {
        method: "PUT",
        body: { planning_capital_inr: capital, usd_inr_rate: rate || null },
      }),
    onSuccess: () => {
      setSaved(true);
      void client.invalidateQueries({ queryKey: ["/settings"] });
      void client.invalidateQueries({ queryKey: ["/paper/account"] });
    },
  });
  return (
    <>
      {save.error && <Alert tone="red">{save.error.message}</Alert>}
      {saved && <Alert tone="green">Planning settings saved.</Alert>}
      {!settings.account_initialized && (
        <Alert tone="blue">
          Initialize a paper account before saving planning settings.{" "}
          <Link href="/paper" className="subtle-link">
            Create account →
          </Link>
        </Alert>
      )}
      <form
        onSubmit={(e) => {
          e.preventDefault();
          save.mutate();
        }}
      >
        <div className="stack">
          <Field
            label="Planning capital (INR)"
            hint="Research input only; not an actual account balance."
          >
            <Input
              type="number"
              min="1"
              step="1"
              value={capital}
              onChange={(e) => setCapital(e.target.value)}
              required
            />
          </Field>
          <Field
            label="Explicit USD / INR reporting rate"
            hint="Optional. Enter a verified rate; the platform does not invent exchange rates."
          >
            <Input
              type="number"
              min="0.01"
              step="0.01"
              value={rate}
              onChange={(e) => setRate(e.target.value)}
              placeholder="Not configured"
            />
          </Field>
        </div>
        <div className="form-actions">
          <Button disabled={save.isPending || !settings.account_initialized}>
            <Save size={14} />
            {save.isPending ? "Saving…" : "Save planning inputs"}
          </Button>
        </div>
      </form>
    </>
  );
}
