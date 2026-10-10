"use client";
import { useState } from "react";
import Link from "next/link";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Activity,
  ArrowUpRight,
  CircleDollarSign,
  DownloadCloud,
  Plus,
  RefreshCw,
  ShieldCheck,
  TrendingUp,
  Wallet,
  X,
} from "lucide-react";
import { api, asRecord, format, useApi } from "@/lib/api";
import type {
  Backtest,
  Candle,
  Dashboard,
  Instrument,
  Order,
  PaperAccount,
  Position,
} from "@/lib/types";
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
  Metric,
  PageTitle,
  Select,
  Status,
} from "./ui";
import { CandleChart, ComparisonChart, EquityChart } from "./charts";
import { MarketStream, DatasetPanel } from "./market-evidence";
import PaperExecutionPanel from "./paper-execution-panel";
export default function TradingPages({ section }: { section: string }) {
  if (section === "terminal") return <TerminalPage />;
  if (section === "paper") return <PaperPage />;
  if (section === "performance") return <PerformancePage />;
  return section === "orders" ? <OrdersPage /> : <PositionsPage />;
}
function PaperPage() {
  const account = useApi<PaperAccount | null>("/paper/account", 10000);
  const dashboard = useApi<Dashboard>("/dashboard", 10000);
  const client = useQueryClient();
  const [initialBalance, setInitialBalance] = useState("10000");
  const [planning, setPlanning] = useState("50000");
  const create = useMutation({
    mutationFn: () =>
      api("/paper/account", {
        method: "POST",
        body: {
          initial_balance: initialBalance,
          settlement_asset: "USD",
          planning_capital_inr: planning,
          usd_inr_rate: null,
        },
      }),
    onSuccess: () => void client.invalidateQueries(),
  });
  const a = account.data,
    asset = a?.settlement_asset ?? "USD";
  return (
    <>
      <PageTitle
        eyebrow="ISOLATED EXECUTION"
        title="Paper trading"
        description="A durable virtual account using real Delta India market data. Your dedicated worker continues when the browser closes."
        action={<Badge tone="blue">SIMULATED FUNDS</Badge>}
      />
      {account.isLoading ? (
        <Loading />
      ) : account.error ? (
        <ErrorState error={account.error} />
      ) : a ? (
        <>
          <div className="grid-4">
            <Metric
              label="Paper equity"
              value={format.money(a.equity, asset)}
              icon={<Wallet />}
              subtext={`Initial capital ${format.money(a.initial_balance, asset)}`}
            />
            <Metric
              label="Available margin"
              value={format.money(a.available_margin, asset)}
              icon={<CircleDollarSign />}
              subtext={`Used ${format.money(a.margin_used, asset)}`}
            />
            <Metric
              label="Realized P&L"
              value={format.money(a.realized_pnl, asset)}
              trend={Number(a.realized_pnl) >= 0}
              icon={<TrendingUp />}
              subtext={`Fees paid ${format.money(a.fees_paid, asset)}`}
            />
            <Metric
              label="Unrealized P&L"
              value={format.money(a.unrealized_pnl, asset)}
              trend={Number(a.unrealized_pnl) >= 0}
              icon={<Activity />}
              subtext="Marked to stored market prices"
            />
          </div>
          {a.emergency_stop && (
            <div className="space-top">
              <Alert tone="red">
                Emergency stop is active. New entries are blocked; open
                positions remain managed. Review risk controls before resuming.
              </Alert>
            </div>
          )}
          {a.reconciliation_required && (
            <Alert tone="red">
              Account reconciliation is required. New exposure is blocked until
              the backend can establish consistent account state.
            </Alert>
          )}
          <Card
            className="space-top"
            title="Paper account equity"
            action={
              <Freshness
                updatedAt={account.dataUpdatedAt}
                fetching={account.isFetching}
              />
            }
          >
            <EquityChart
              points={dashboard.data?.equity_curve ?? []}
              asset={asset}
            />
          </Card>
          <div className="grid-2 space-top">
            <Card
              title="Capital & settlement"
              subtitle="Configured inputs; no implied exchange balance"
            >
              <div className="panel-body">
                <div className="health-row">
                  <span>Settlement currency</span>
                  <strong>{asset}</strong>
                </div>
                <div className="health-row">
                  <span>Simulated cash</span>
                  <strong>{format.money(a.cash, asset)}</strong>
                </div>
                <div className="health-row">
                  <span>INR planning budget</span>
                  <strong>{format.money(a.planning_capital_inr, "INR")}</strong>
                </div>
                <div className="health-row">
                  <span>Explicit USD/INR rate</span>
                  <strong>
                    {a.usd_inr_rate
                      ? format.number(a.usd_inr_rate)
                      : "Not configured"}
                  </strong>
                </div>
                <p className="list-note">
                  Paper account currency must match verified contract
                  settlement. Planning capital is separate and does not fund
                  this account.
                </p>
              </div>
            </Card>
            <Card title="Next steps">
              <div className="panel-body">
                <p className="list-note">
                  Create, validate and backtest a strategy. Starting it in paper
                  mode activates the independent execution worker after backend
                  approval checks.
                </p>
                <div className="button-group">
                  <Link className="btn btn-primary" href="/strategies">
                    <Plus size={13} />
                    Manage strategies
                  </Link>
                  <Link className="btn btn-secondary" href="/risk">
                    <ShieldCheck size={13} />
                    Risk controls
                  </Link>
                </div>
              </div>
            </Card>
          </div>
          <PositionsTable embedded />
          <PaperExecutionPanel />
        </>
      ) : (
        <Card
          title="Initialize your virtual account"
          subtitle="One durable account; capital adjustments preserve trading history"
        >
          <div className="panel-body" style={{ maxWidth: 670 }}>
            <Alert tone="blue">
              The USD balance below is simulated capital. ₹50,000 is an editable
              planning example and will not be silently converted into a dollar
              balance.
            </Alert>
            {create.error && <Alert tone="red">{create.error.message}</Alert>}
            <form
              onSubmit={(e) => {
                e.preventDefault();
                create.mutate();
              }}
            >
              <div className="form-grid">
                <Field label="Initial simulated balance (USD)">
                  <Input
                    type="number"
                    min="100"
                    step="100"
                    value={initialBalance}
                    onChange={(e) => setInitialBalance(e.target.value)}
                    required
                  />
                </Field>
                <Field label="Planning budget (INR)">
                  <Input
                    type="number"
                    min="1"
                    value={planning}
                    onChange={(e) => setPlanning(e.target.value)}
                    required
                  />
                </Field>
              </div>
              <div className="form-actions">
                <Button disabled={create.isPending}>
                  <Wallet size={14} />
                  {create.isPending
                    ? "Creating account…"
                    : "Create paper account"}
                </Button>
              </div>
            </form>
          </div>
        </Card>
      )}
    </>
  );
}
function PositionsPage() {
  return (
    <>
      <PageTitle
        eyebrow="EXPOSURE & PROTECTION"
        title="Positions"
        description="Environment specific positions with captured contract economics, stop loss and take profit protection."
        action={<Badge tone="blue">PAPER · SIMULATED</Badge>}
      />
      <PositionsTable />
    </>
  );
}
function PositionsTable({ embedded = false }: { embedded?: boolean }) {
  const [status, setStatus] = useState("OPEN");
  const query = useApi<Position[]>(`/positions?status=${status}`, 10000);
  const account = useApi<PaperAccount | null>("/paper/account", 15000);
  const client = useQueryClient();
  const [closing, setClosing] = useState<Position | null>(null);
  const close = useMutation({
    mutationFn: () =>
      api(`/positions/${closing?.id}/close`, { method: "POST", body: {} }),
    onSuccess: () => {
      setClosing(null);
      void client.invalidateQueries();
    },
  });
  const asset = account.data?.settlement_asset ?? "USD";
  const pnl = (p: Position) =>
    p.status === "CLOSED"
      ? p.realized_pnl
      : (Number(p.mark_price) - Number(p.entry_price)) *
        Number(p.quantity) *
        Number(p.contract_size) *
        (p.side === "BUY" ? 1 : -1) *
        Number(p.conversion_rate ?? 1);
  return (
    <>
      <div className={embedded ? "toolbar space-top" : "toolbar"}>
        <Select
          value={status}
          onChange={(e) => setStatus(e.target.value)}
          aria-label="Position status"
          style={{ width: 200 }}
        >
          <option value="OPEN">Open positions</option>
          <option value="CLOSED">Closed positions</option>
        </Select>
        <div className="spacer" />
        <Freshness
          updatedAt={query.dataUpdatedAt}
          fetching={query.isFetching}
        />
      </div>
      <Card
        title={
          status === "OPEN" ? "Managed open positions" : "Closed positions"
        }
        subtitle="Closing a position uses a fresh verified market price and simulated execution costs"
      >
        {query.isLoading ? (
          <Loading />
        ) : query.error ? (
          <div className="panel-body">
            <ErrorState
              error={query.error}
              retry={() => void query.refetch()}
            />
          </div>
        ) : (
          <DataTable<Position>
            rows={query.data ?? []}
            getKey={(p) => p.id}
            empty={
              status === "OPEN"
                ? "No open paper positions"
                : "No closed paper positions"
            }
            columns={[
              {
                label: "Market",
                render: (p) => (
                  <div>
                    <strong>{p.symbol}</strong>
                    <div className="table-sub">
                      Strategy #{p.strategy_id ?? "—"}
                    </div>
                  </div>
                ),
              },
              {
                label: "Side / qty",
                render: (p) => (
                  <div>
                    <Badge tone={p.side === "BUY" ? "green" : "red"}>
                      {p.side}
                    </Badge>
                    <div className="table-sub">
                      {format.number(p.quantity, 0)} contracts
                    </div>
                  </div>
                ),
              },
              {
                label: "Entry / mark",
                render: (p) => (
                  <div>
                    {format.money(p.entry_price)}
                    <div className="table-sub">
                      Mark {format.money(p.mark_price)}
                    </div>
                  </div>
                ),
              },
              {
                label: "P&L",
                render: (p) => (
                  <span
                    className={Number(pnl(p)) >= 0 ? "text-green" : "text-red"}
                  >
                    {format.money(pnl(p), asset)}
                  </span>
                ),
              },
              {
                label: "Protection",
                render: (p) => (
                  <div>
                    SL {format.number(p.stop_price)}
                    <div className="table-sub">
                      TP {format.number(p.take_profit_price)}
                    </div>
                  </div>
                ),
              },
              { label: "Margin", render: (p) => format.money(p.margin, asset) },
              {
                label: "State",
                render: (p) => (
                  <div>
                    <Status value={p.status} />
                    <div className="table-sub">
                      {p.protection_verified
                        ? "Protection verified"
                        : "Review protection"}
                    </div>
                  </div>
                ),
              },
              {
                label: "Manage",
                render: (p) =>
                  p.status === "OPEN" ? (
                    <Button
                      variant="danger"
                      className="btn-small"
                      onClick={() => setClosing(p)}
                    >
                      <X size={12} />
                      Close
                    </Button>
                  ) : (
                    format.date(p.opened_at)
                  ),
              },
            ]}
          />
        )}
      </Card>
      <Dialog
        open={!!closing}
        onOpenChange={(open) => !open && setClosing(null)}
        title="Close this paper position?"
        description="A fresh stored market price is required. The backend simulates fees and slippage, closes the managed position and cancels its protective paper orders."
      >
        {close.error && <Alert tone="red">{close.error.message}</Alert>}
        <Alert tone="amber">
          {closing?.symbol} · {closing?.side} ·{" "}
          {format.number(closing?.quantity, 0)} contracts. The final simulated
          fill can differ from the current mark.
        </Alert>
        <div className="form-actions">
          <Button variant="secondary" onClick={() => setClosing(null)}>
            Cancel
          </Button>
          <Button
            variant="danger"
            disabled={close.isPending}
            onClick={() => close.mutate()}
          >
            {close.isPending ? "Closing…" : "Confirm paper close"}
          </Button>
        </div>
      </Dialog>
    </>
  );
}
function OrdersPage() {
  const query = useApi<Order[]>("/orders", 10000);
  const [filter, setFilter] = useState("ALL");
  const [detail, setDetail] = useState<Order | null>(null);
  const data = query.data ?? [],
    rows = data.filter((o) => filter === "ALL" || o.status === filter);
  return (
    <>
      <PageTitle
        eyebrow="ORDER MANAGEMENT"
        title="Orders"
        description="Persisted simulated entries, protective orders and reduce only exits, with stable client order identifiers."
        action={<Badge tone="blue">PAPER · SIMULATED</Badge>}
      />
      <div className="toolbar">
        <Select
          aria-label="Filter order status"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          style={{ width: 200 }}
        >
          <option value="ALL">All statuses</option>
          {[...new Set(data.map((o) => o.status))].map((s) => (
            <option key={s}>{s}</option>
          ))}
        </Select>
        <div className="spacer" />
        <Freshness
          updatedAt={query.dataUpdatedAt}
          fetching={query.isFetching}
        />
      </div>
      <Card
        title="Order history"
        subtitle="Protective orders remain in place when strategies are paused or stopped"
      >
        {query.isLoading ? (
          <Loading />
        ) : query.error ? (
          <div className="panel-body">
            <ErrorState error={query.error} />
          </div>
        ) : (
          <DataTable<Order>
            rows={rows}
            getKey={(o) => o.id}
            empty="No paper orders have been recorded"
            columns={[
              {
                label: "Order",
                render: (o) => (
                  <button
                    className="subtle-link mono"
                    style={{ border: 0, background: "none", padding: 0 }}
                    onClick={() => setDetail(o)}
                  >
                    {o.id.slice(0, 8)}
                  </button>
                ),
              },
              {
                label: "Instrument",
                render: (o) => <strong>{o.symbol}</strong>,
              },
              {
                label: "Side",
                render: (o) => (
                  <Badge tone={o.side === "BUY" ? "green" : "red"}>
                    {o.side}
                  </Badge>
                ),
              },
              {
                label: "Type",
                render: (o) => o.order_type?.replaceAll("_", " "),
              },
              {
                label: "Contracts",
                render: (o) => format.number(o.quantity, 0),
              },
              { label: "Price", render: (o) => format.money(o.price) },
              { label: "Status", render: (o) => <Status value={o.status} /> },
              { label: "Created", render: (o) => format.date(o.created_at) },
            ]}
          />
        )}
      </Card>
      <Dialog
        open={!!detail}
        onOpenChange={(open) => !open && setDetail(null)}
        title="Paper order record"
        description="Persisted order identity and state; no production order was submitted."
      >
        <JsonView value={detail} />
      </Dialog>
    </>
  );
}
function TerminalPage() {
  const [symbol, setSymbol] = useState("BTCUSD");
  const [timeframe, setTimeframe] = useState(60);
  const [message, setMessage] = useState<string>();
  const client = useQueryClient();
  const candles = useApi<Candle[]>(
    `/market/candles?symbol=${symbol}&timeframe_seconds=${timeframe}&limit=500`,
    15000,
  );
  const instruments = useApi<Instrument[]>("/instruments", 60000);
  const instrument = instruments.data?.find((i) => i.symbol === symbol);
  const refresh = useMutation({
    mutationFn: () =>
      api<Record<string, unknown>>("/instruments/refresh", {
        method: "POST",
        body: {},
      }),
    onSuccess: (r) => {
      setMessage(
        `Discovered ${r.count ?? "verified"} instruments from Delta Exchange India.`,
      );
      void client.invalidateQueries({ queryKey: ["/instruments"] });
    },
  });
  const history = useMutation({
    mutationFn: () =>
      api<Record<string, unknown>>("/market/import", {
        method: "POST",
        body: { symbol, timeframe_seconds: timeframe, lookback_bars: 500 },
      }),
    onSuccess: (r) => {
      setMessage(
        `Imported ${r.imported ?? "historical"} closed candles from Delta India.`,
      );
      void client.invalidateQueries({
        queryKey: [
          `/market/candles?symbol=${symbol}&timeframe_seconds=${timeframe}&limit=500`,
        ],
      });
    },
  });
  const latest = candles.data?.at(-1),
    old = latest?.timestamp
      ? (Date.now() - new Date(latest.timestamp).getTime()) / 1000 >
        timeframe * 2
      : true;
  return (
    <>
      <PageTitle
        eyebrow="DELTA INDIA PUBLIC MARKET DATA"
        title="Market terminal"
        description="Verified contract discovery and interactive historical candlesticks. Order placement is controlled by backend strategy and environment gates."
        action={
          <Button
            variant="secondary"
            disabled={refresh.isPending}
            onClick={() => refresh.mutate()}
          >
            <RefreshCw size={14} />
            {refresh.isPending ? "Discovering…" : "Refresh contracts"}
          </Button>
        }
      />
      <MarketStream />
      {message && <Alert tone="green">{message}</Alert>}
      {(refresh.error || history.error) && (
        <Alert tone="red">
          {refresh.error?.message ?? history.error?.message}
        </Alert>
      )}
      <div className="toolbar">
        <Select
          value={symbol}
          onChange={(e) => setSymbol(e.target.value)}
          aria-label="Instrument"
          style={{ width: 170 }}
        >
          <option>BTCUSD</option>
          <option>ETHUSD</option>
        </Select>
        <Select
          value={timeframe}
          onChange={(e) => setTimeframe(Number(e.target.value))}
          aria-label="Candle timeframe"
          style={{ width: 160 }}
        >
          <option value="60">1 minute</option>
          <option value="300">5 minutes</option>
          <option value="900">15 minutes</option>
          <option value="3600">1 hour</option>
          <option value="14400">4 hours</option>
        </Select>
        <Button
          variant="secondary"
          onClick={() => history.mutate()}
          disabled={history.isPending}
        >
          <DownloadCloud size={14} />
          {history.isPending ? "Importing…" : "Import closed candles"}
        </Button>
        <div className="spacer" />
        <Freshness
          updatedAt={candles.dataUpdatedAt}
          fetching={candles.isFetching}
        />
      </div>
      {old && (
        <Alert tone="amber">
          This is stored historical candle data. The most recent candle is{" "}
          {latest ? `${format.date(latest.timestamp)} IST` : "unavailable"}; use
          fresh backend market data before paper execution.
        </Alert>
      )}
      <div className="grid-4">
        <Metric
          label={`${symbol} latest close`}
          value={format.money(latest?.close)}
          subtext={
            latest ? `${format.date(latest.timestamp)} IST` : "No data imported"
          }
        />
        <Metric
          label="Candle high / low"
          value={`${format.number(latest?.high)} / ${format.number(latest?.low)}`}
          subtext={`${timeframe / 60} minute closed candle`}
        />
        <Metric
          label="Contract size"
          value={format.number(
            instrument?.contract_value ?? instrument?.contract_size,
            6,
          )}
          subtext={String(
            instrument?.contract_unit_currency ??
              instrument?.underlying_asset ??
              "Awaiting discovery",
          )}
        />
        <Metric
          label="Tick size"
          value={format.number(instrument?.tick_size, 4)}
          subtext={
            instrument
              ? `Product ID ${instrument.id}`
              : "Awaiting contract discovery"
          }
        />
      </div>
      <Card
        className="space-top"
        title={`${symbol} · Candles & volume`}
        subtitle="Drag to pan · scroll to zoom · crosshair to inspect · timestamps are chart timezone (UTC)"
        action={<Badge tone="blue">DELTA INDIA HISTORY</Badge>}
      >
        {candles.isLoading ? (
          <Loading />
        ) : candles.error ? (
          <div className="panel-body">
            <ErrorState error={candles.error} />
          </div>
        ) : (
          <CandleChart candles={candles.data ?? []} />
        )}
      </Card>
      <div className="grid-2 space-top">
        <Card
          title="Verified instrument economics"
          subtitle="Raw verified backend metadata, without inferred contract multipliers"
        >
          <div className="panel-body">
            {instruments.error ? (
              <ErrorState error={instruments.error} />
            ) : instrument ? (
              <JsonView value={instrument} />
            ) : (
              <Empty
                title="Contract metadata is not available"
                description="Use Refresh contracts to discover the instrument from Delta India."
              />
            )}
          </div>
        </Card>
        <Card
          title="Execution access"
          subtitle="Research terminal; routing remains independently restricted"
        >
          <div className="panel-body">
            <Alert tone="blue">
              Internal paper execution uses approved strategy signals, contract
              metadata and risk sizing. This terminal does not submit exchange
              orders.
            </Alert>
            <div className="health-row">
              <span>Paper strategies</span>
              <Badge tone="blue">AVAILABLE</Badge>
            </div>
            <div className="health-row">
              <span>Demo manual orders</span>
              <Link href="/demo" className="subtle-link">
                Review demo access →
              </Link>
            </div>
            <div className="health-row">
              <span>Live manual orders</span>
              <Badge tone="red">DISABLED</Badge>
            </div>
            <Link
              className="btn btn-secondary"
              style={{ marginTop: 20 }}
              href="/strategies"
            >
              <ArrowUpRight size={14} />
              Configure a paper strategy
            </Link>
          </div>
        </Card>
      </div>
      <DatasetPanel />
    </>
  );
}
function PerformancePage() {
  const dashboard = useApi<Dashboard>("/dashboard", 10000);
  const backtests = useApi<Backtest[]>("/backtests", 15000);
  const d = dashboard.data,
    asset = d?.account?.settlement_asset ?? "USD";
  const comparison = (backtests.data ?? [])
    .filter((b) => b.status === "COMPLETED")
    .slice(0, 12)
    .map((b) => ({
      name: `Run ${b.id}`,
      value: Number(asRecord(asRecord(b.result).metrics).net_return ?? 0) * 100,
    }));
  return (
    <>
      <PageTitle
        eyebrow="RESULTS & RESEARCH"
        title="Performance analytics"
        description="Recorded paper results and historical simulations remain separate. Compare strategy hypotheses with equivalent datasets and cost assumptions."
        action={<Badge tone="purple">RESEARCH METRICS</Badge>}
      />
      {dashboard.isLoading ? (
        <Loading />
      ) : dashboard.error ? (
        <ErrorState error={dashboard.error} />
      ) : (
        <>
          <div className="grid-4">
            <Metric
              label="Realized paper P&L"
              value={format.money(d?.account?.realized_pnl, asset)}
              subtext="Simulated account results"
            />
            <Metric
              label="Completed paper trades"
              value={format.number(d?.metrics.total_trades, 0)}
              subtext="Durable closed positions"
            />
            <Metric
              label="Paper win rate"
              value={
                d?.metrics.win_rate !== null &&
                d?.metrics.win_rate !== undefined
                  ? `${format.number(d.metrics.win_rate, 1)}%`
                  : "—"
              }
              subtext="Unavailable until a position is closed"
            />
            <Metric
              label="Current drawdown"
              value={
                d?.metrics.drawdown !== undefined &&
                d?.metrics.drawdown !== null
                  ? `${format.number(Number(d.metrics.drawdown) * 100, 2)}%`
                  : "—"
              }
              subtext="From paper equity high water mark"
            />
          </div>
          <Card
            className="space-top"
            title="Recorded paper equity"
            action={<Badge tone="blue">SIMULATED</Badge>}
          >
            <EquityChart points={d?.equity_curve ?? []} asset={asset} />
          </Card>
        </>
      )}
      <Card
        className="space-top"
        title="Recent backtest returns (%)"
        subtitle="Each run retains independent simulation settings. This chart does not establish statistical comparability."
      >
        {backtests.isLoading ? (
          <Loading />
        ) : backtests.error ? (
          <div className="panel-body">
            <ErrorState error={backtests.error} />
          </div>
        ) : (
          <ComparisonChart data={comparison} />
        )}
      </Card>
      <Alert tone="amber">
        Sharpe, Sortino, profit factor and other research metrics are available
        in each backtest report where sufficient observations exist. Missing
        statistics are not replaced with invented values.
      </Alert>
    </>
  );
}
