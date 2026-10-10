"use client";
import Link from "next/link";
import {
  Activity,
  ArrowUpRight,
  Bot,
  FlaskConical,
  Plus,
  ShieldAlert,
  TrendingUp,
  Wallet,
  Layers,
  Database,
  Radio,
} from "lucide-react";
import { useApi, format, asRecord } from "@/lib/api";
import type { Dashboard, Instrument, Order, Strategy } from "@/lib/types";
import {
  Alert,
  Badge,
  Button,
  Card,
  DataTable,
  Empty,
  ErrorState,
  Freshness,
  Loading,
  Metric,
  PageTitle,
  SectionLink,
  Status,
} from "./ui";
import { EquityChart } from "./charts";
export default function DashboardPage({
  onEmergency,
}: {
  onEmergency: () => void;
}) {
  const query = useApi<Dashboard>("/dashboard", 10000);
  const instruments = useApi<Instrument[]>("/instruments", 60000);
  if (query.isLoading) return <Loading label="Loading the command center…" />;
  if (query.error || !query.data)
    return (
      <ErrorState error={query.error} retry={() => void query.refetch()} />
    );
  const d = query.data,
    a = d.account,
    m = d.metrics ?? {},
    asset = a?.settlement_asset ?? "USD",
    worker = asRecord(d.worker),
    market = asRecord(d.market_data);
  const running = d.strategies.filter((s) => s.status === "RUNNING").length;
  return (
    <>
      <PageTitle
        eyebrow="YOUR TRADING WORKSPACE"
        title="Command center"
        description="A clear view of your strategies, simulated account and operational health."
        action={
          <>
            <Link href="/strategies" className="btn btn-primary">
              <Plus size={14} />
              New strategy
            </Link>
            <Button variant="danger" onClick={onEmergency}>
              <ShieldAlert size={14} />
              Emergency stop
            </Button>
          </>
        }
      />
      <div className="paper-banner">
        <FlaskConical size={14} />
        <span>
          <strong>Paper trading</strong> · All account values and executions
          below are simulated. Public market data comes from Delta Exchange
          India.
        </span>
        <Badge tone="blue">SIMULATED</Badge>
      </div>
      {!a && (
        <Alert tone="blue">
          Create a paper account to begin. Choose your own settlement balance
          and keep your INR planning budget separate.{" "}
          <Link href="/paper" className="subtle-link">
            Set up paper trading →
          </Link>
        </Alert>
      )}
      <div className="grid-4">
        <Metric
          label="Account equity"
          value={format.money(a?.equity, asset)}
          icon={<Wallet />}
          subtext={<>Balance {format.money(a?.cash, asset)}</>}
        />
        <Metric
          label="Realized P&L"
          value={format.money(a?.realized_pnl ?? m.realized_pnl, asset)}
          icon={<TrendingUp />}
          trend={
            a?.realized_pnl !== undefined
              ? Number(a.realized_pnl) >= 0
              : undefined
          }
          subtext="Simulated, after recorded fees"
        />
        <Metric
          label="Active strategies"
          value={running}
          icon={<Bot />}
          subtext={`${d.strategies.length} total · ${d.strategies.filter((s) => s.status === "PAUSED").length} paused`}
        />
        <Metric
          label="Open positions"
          value={d.positions.length}
          icon={<Layers />}
          subtext={`Margin used ${format.money(a?.margin_used, asset)}`}
        />
      </div>
      <div className="dashboard-main space-top">
        <div className="stack">
          <Card
            title="Portfolio equity"
            subtitle="Recorded paper account snapshots · settlement currency"
            action={<Badge tone="blue">SIMULATED</Badge>}
          >
            <div className="chart-header">
              <div>
                <div className="chart-label">Current equity</div>
                <div className="chart-value">
                  {format.money(a?.equity, asset)}
                </div>
              </div>
              <div className="chart-legend">
                <span className="legend-dot" />
                Paper equity
              </div>
            </div>
            <EquityChart points={d.equity_curve ?? []} asset={asset} />
          </Card>
          <Card
            title="Strategy fleet"
            subtitle="Independent versions and shared portfolio risk checks"
            action={
              <SectionLink href="/strategies">Manage strategies</SectionLink>
            }
          >
            <DataTable<Strategy>
              rows={d.strategies.slice(0, 8)}
              getKey={(s) => s.id}
              empty="Your first strategy starts here"
              columns={[
                {
                  label: "Strategy",
                  render: (s) => (
                    <div>
                      <div className="table-name">{s.name}</div>
                      <div className="table-sub">
                        {s.config.kind?.replaceAll("_", " ")} · v{s.version}
                      </div>
                    </div>
                  ),
                },
                {
                  label: "Market",
                  render: (s) => (
                    <div>
                      {s.config.symbol}
                      <div className="table-sub">
                        {s.config.timeframe_seconds / 60} min
                      </div>
                    </div>
                  ),
                },
                { label: "Status", render: (s) => <Status value={s.status} /> },
                {
                  label: "Environment",
                  render: () => <Badge tone="blue">PAPER</Badge>,
                },
              ]}
            />
            {!d.strategies.length && (
              <div className="collapse-note">
                <Link className="subtle-link" href="/strategies">
                  Create and validate a strategy →
                </Link>
              </div>
            )}
          </Card>
        </div>
        <div className="stack">
          <Card
            title="Market watch"
            subtitle="Discovered Delta India perpetual contracts"
            action={<SectionLink href="/terminal">Terminal</SectionLink>}
          >
            {instruments.isLoading ? (
              <Loading />
            ) : instruments.error ? (
              <div className="panel-body">
                <ErrorState error={instruments.error} />
              </div>
            ) : instruments.data?.length ? (
              instruments.data
                .filter((i) => ["BTCUSD", "ETHUSD"].includes(i.symbol))
                .map((i) => (
                  <div className="market-row" key={i.id}>
                    <div className="market-name">
                      <span
                        className={`coin-symbol ${i.symbol.startsWith("ETH") ? "eth" : ""}`}
                      >
                        {i.symbol.startsWith("BTC") ? "₿" : "Ξ"}
                      </span>
                      <div>
                        <h3>{i.symbol}</h3>
                        <div className="market-sub">
                          Delta India · {i.contract_type ?? "futures"}
                        </div>
                      </div>
                    </div>
                    <div className="market-value">
                      <Status
                        value={
                          i.state === "live"
                            ? "TRADABLE"
                            : (i.state ?? i.trading_status ?? "DISCOVERED")
                        }
                      />
                      <div className="market-sub">
                        Tick {format.number(i.tick_size, 2)}
                      </div>
                    </div>
                  </div>
                ))
            ) : (
              <Empty
                title="No instruments discovered"
                description="Open the terminal to fetch public instrument metadata."
              />
            )}
          </Card>
          <Card
            title="Operational status"
            subtitle="Latest backend health evidence"
          >
            <div className="panel-body">
              <div className="health-row">
                <span className="health-label">
                  <Activity />
                  Trading worker
                </span>
                <Status
                  value={String(
                    worker.status ??
                      (worker.heartbeat_at ? "HEALTHY" : "UNKNOWN"),
                  )}
                />
              </div>
              <div className="health-row">
                <span className="health-label">
                  <Radio />
                  Market data
                </span>
                <Status
                  value={String(
                    market.status ?? (market.stale ? "STALE" : "HEALTHY"),
                  )}
                />
              </div>
              <div className="health-row">
                <span className="health-label">
                  <Database />
                  Execution mode
                </span>
                <Badge tone="blue">PAPER</Badge>
              </div>
              <div className="health-row">
                <span className="health-label">
                  <ShieldAlert />
                  Live execution
                </span>
                <Badge>DISABLED</Badge>
              </div>
              <div style={{ marginTop: 17 }}>
                <SectionLink href="/system-health">
                  View system health
                </SectionLink>
              </div>
            </div>
          </Card>
          <Card title="Research before deployment">
            <div className="panel-body">
              <p style={{ fontSize: 11, lineHeight: 1.9 }}>
                Validate a strategy version and run it against historical Delta
                India candles before starting autonomous paper trading.
              </p>
              <Link
                href="/backtesting"
                className="btn btn-secondary"
                style={{ marginTop: 16, width: "100%" }}
              >
                <FlaskConical size={14} />
                Open backtesting lab
                <ArrowUpRight size={14} />
              </Link>
            </div>
          </Card>
        </div>
      </div>
      <Card
        className="space-top"
        title="Recent orders"
        subtitle="Durable paper order lifecycle; exchange orders are not submitted"
        action={<SectionLink href="/orders">All orders</SectionLink>}
      >
        <DataTable<Order>
          rows={d.recent_orders.slice(0, 8)}
          getKey={(o) => o.id}
          empty="No paper orders have been placed"
          columns={[
            { label: "Instrument", render: (o) => <strong>{o.symbol}</strong> },
            {
              label: "Side",
              render: (o) => (
                <Badge tone={o.side.toUpperCase() === "BUY" ? "green" : "red"}>
                  {o.side}
                </Badge>
              ),
            },
            { label: "Quantity", render: (o) => format.number(o.quantity, 0) },
            {
              label: "Fill price",
              render: (o) => format.money(o.fill_price ?? o.price, asset),
            },
            { label: "Status", render: (o) => <Status value={o.status} /> },
            { label: "Created", render: (o) => format.date(o.created_at) },
          ]}
        />
      </Card>
      <div style={{ marginTop: 17 }}>
        <Freshness
          updatedAt={query.dataUpdatedAt}
          fetching={query.isFetching}
        />
      </div>
    </>
  );
}
