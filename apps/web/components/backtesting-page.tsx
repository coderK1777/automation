"use client";
import { useState } from "react";
import { useSearchParams } from "next/navigation";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  DownloadCloud,
  Play,
  CircleDollarSign,
  TrendingUp,
  Activity,
  ArrowDownRight,
} from "lucide-react";
import { api, asArray, asRecord, format, useApi } from "@/lib/api";
import type { Strategy, Backtest, Point } from "@/lib/types";
import {
  Alert,
  Badge,
  Button,
  Card,
  DataTable,
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
  Textarea,
} from "./ui";
import { EquityChart } from "./charts";
import ResearchRuns from "./research-runs";
export default function BacktestingPage() {
  const params = useSearchParams();
  const strategies = useApi<Strategy[]>("/strategies", 15000);
  const runs = useApi<Backtest[]>("/backtests", 10000);
  const datasets = useApi<Record<string, unknown>[]>("/market/datasets", 30000);
  const client = useQueryClient();
  const [strategyId, setStrategyId] = useState(params.get("strategy") ?? "");
  const [capital, setCapital] = useState("10000");
  const [limit, setLimit] = useState(1500);
  const [dataset, setDataset] = useState("");
  const [feePercent, setFeePercent] = useState("0.05");
  const [spread, setSpread] = useState("1");
  const [slippage, setSlippage] = useState("2");
  const [funding, setFunding] = useState("[]");
  const [selected, setSelected] = useState<Backtest | null>(null);
  const [imported, setImported] = useState<string>();
  const strategy = strategies.data?.find((s) => String(s.id) === strategyId);
  const fetchHistory = useMutation({
    mutationFn: async () => {
      if (!strategy)
        throw new Error("Choose a strategy before importing history.");
      const timeframes = [
        strategy.config.timeframe_seconds,
        ...asArray<Record<string, unknown>>(
          strategy.config.higher_timeframes,
        ).map((value) => Number(value.timeframe_seconds)),
      ];
      let importedCount = 0;
      for (const timeframe of [...new Set(timeframes)]) {
        const result = await api<Record<string, unknown>>("/market/import", {
          method: "POST",
          body: {
            symbol: strategy.config.symbol,
            timeframe_seconds: timeframe,
            lookback_bars: limit,
          },
        });
        importedCount += Number(result.imported ?? 0);
      }
      return { imported: importedCount };
    },
    onSuccess: (r) => {
      const result = asRecord(r);
      setImported(
        `Historical import completed${result.imported !== undefined ? `: ${result.imported} candles stored` : ""}. Source: Delta Exchange India.`,
      );
      void client.invalidateQueries({ queryKey: ["/market/candles"] });
    },
  });
  const run = useMutation({
    mutationFn: () => {
      const fundingEvents: unknown = JSON.parse(funding);
      if (!Array.isArray(fundingEvents))
        throw new Error(
          "Funding schedule must be a JSON array of attributed events.",
        );
      return api<Backtest>("/backtests", {
        method: "POST",
        body: {
          strategy_id: Number(strategyId),
          initial_capital: capital,
          limit,
          dataset_id: dataset || undefined,
          fee_rate: String(Number(feePercent) / 100),
          spread_bps: spread,
          slippage_bps: slippage,
          funding_events: fundingEvents,
        },
      });
    },
    onSuccess: (data) => {
      setSelected(data);
      void client.invalidateQueries({ queryKey: ["/backtests"] });
      void client.invalidateQueries({ queryKey: ["/strategies"] });
    },
  });
  return (
    <>
      <PageTitle
        eyebrow="HISTORICAL RESEARCH"
        title="Backtesting lab"
        description="Test a saved strategy against stored Delta India candles, with contract sizing, fees and explicit model limitations."
        action={<Badge tone="purple">HISTORICAL SIMULATION</Badge>}
      />
      <Alert tone="amber">
        Backtest results are simulated research evidence. They do not predict
        future returns or approve demo or live execution. Historical funding and
        intrabar execution may have model limitations disclosed in each result.
      </Alert>
      <div className="grid-2">
        <Card
          title="Configure a run"
          subtitle="Use a validated strategy version and real historical market data"
        >
          <div className="panel-body">
            {strategies.error && <ErrorState error={strategies.error} />}
            <form
              onSubmit={(e) => {
                e.preventDefault();
                run.mutate();
              }}
            >
              <div className="form-grid">
                <div className="form-full">
                  <Field label="Strategy">
                    <Select
                      value={strategyId}
                      onChange={(e) => {
                        setStrategyId(e.target.value);
                        setDataset("");
                        setImported(undefined);
                      }}
                      required
                    >
                      <option value="">Choose a saved strategy</option>
                      {strategies.data?.map((s) => (
                        <option key={s.id} value={s.id}>
                          {s.name} · v{s.version} · {s.status}
                        </option>
                      ))}
                    </Select>
                  </Field>
                </div>
                <Field
                  label="Initial capital (USD)"
                  hint="Independent simulated capital; no INR conversion assumed."
                >
                  <Input
                    type="number"
                    min="100"
                    step="100"
                    value={capital}
                    onChange={(e) => setCapital(e.target.value)}
                    required
                  />
                </Field>
                <Field
                  label="Historical candle limit"
                  hint="Most recent stored candles in chronological order."
                >
                  <Input
                    type="number"
                    min="100"
                    max="5000"
                    step="100"
                    value={limit}
                    onChange={(e) => setLimit(Number(e.target.value))}
                    required
                  />
                </Field>
                <div className="form-full">
                  <Field label="Pinned immutable dataset">
                    <Select
                      value={dataset}
                      onChange={(e) => setDataset(e.target.value)}
                    >
                      <option value="">
                        Snapshot stored candles for this run
                      </option>
                      {datasets.data
                        ?.filter(
                          (v) =>
                            v.symbol === strategy?.config.symbol &&
                            Number(v.timeframe_seconds) ===
                              strategy?.config.timeframe_seconds,
                        )
                        .map((v) => (
                          <option key={String(v.id)} value={String(v.id)}>
                            #{String(v.id).slice(0, 12)} ·{" "}
                            {String(v.candle_count)} candles ·{" "}
                            {v.complete ? "complete" : "gaps"}
                          </option>
                        ))}
                    </Select>
                  </Field>
                </div>
                <Field label="Fee per fill (%)">
                  <Input
                    required
                    type="number"
                    step="0.001"
                    min="0"
                    max="5"
                    value={feePercent}
                    onChange={(e) => setFeePercent(e.target.value)}
                  />
                </Field>
                <Field label="Spread (basis points)">
                  <Input
                    required
                    type="number"
                    step="0.1"
                    min="0"
                    max="1000"
                    value={spread}
                    onChange={(e) => setSpread(e.target.value)}
                  />
                </Field>
                <Field label="Adverse slippage (basis points)">
                  <Input
                    required
                    type="number"
                    step="0.1"
                    min="0"
                    max="1000"
                    value={slippage}
                    onChange={(e) => setSlippage(e.target.value)}
                  />
                </Field>
                <div className="form-full">
                  <details>
                    <summary>Attributed historical funding schedule</summary>
                    <Field
                      label="Funding events JSON"
                      hint="Use only verified historical rates and timestamps. Empty means unavailable funding evidence, disclosed in the result."
                    >
                      <Textarea
                        className="code-input"
                        value={funding}
                        onChange={(e) => setFunding(e.target.value)}
                      />
                    </Field>
                  </details>
                </div>
              </div>
              {strategy && (
                <div className="detail-metadata" style={{ padding: "18px 0" }}>
                  <div>
                    <small>Instrument</small>
                    <strong>{strategy.config.symbol}</strong>
                  </div>
                  <div>
                    <small>Timeframe</small>
                    <strong>
                      {strategy.config.timeframe_seconds / 60} min
                    </strong>
                  </div>
                  <div>
                    <small>Model</small>
                    <strong>{strategy.config.kind.replaceAll("_", " ")}</strong>
                  </div>
                </div>
              )}
              {fetchHistory.error && (
                <Alert tone="red">{fetchHistory.error.message}</Alert>
              )}
              {run.error && <Alert tone="red">{run.error.message}</Alert>}
              {imported && <Alert tone="green">{imported}</Alert>}
              <div className="form-actions">
                <Button
                  type="button"
                  variant="secondary"
                  disabled={
                    !strategy || fetchHistory.isPending || run.isPending
                  }
                  onClick={() => fetchHistory.mutate()}
                >
                  <DownloadCloud size={14} />
                  {fetchHistory.isPending
                    ? "Importing candles…"
                    : "Import Delta history"}
                </Button>
                <Button
                  type="submit"
                  disabled={
                    !strategy || run.isPending || fetchHistory.isPending
                  }
                >
                  <Play size={14} />
                  {run.isPending ? "Running simulation…" : "Run backtest"}
                </Button>
              </div>
            </form>
          </div>
        </Card>
        <Card
          title="Research workflow"
          subtitle="From a hypothesis to evidence"
        >
          <div className="panel-body">
            {[
              [
                "01",
                "Create & validate",
                "Define a deterministic strategy and validate its parameters.",
              ],
              [
                "02",
                "Import market history",
                "Retrieve real candle data from Delta India. The source and time range remain auditable.",
              ],
              [
                "03",
                "Evaluate the result",
                "Review trade count, fees, drawdown, equity and model limitations. An empty trade set is a valid result.",
              ],
              [
                "04",
                "Deploy in paper mode",
                "The exact backtested version and independent risk limits are checked by the backend.",
              ],
            ].map(([n, title, text]) => (
              <div
                className="readiness-item"
                key={n}
                style={{ alignItems: "flex-start" }}
              >
                <Badge tone="purple">{n}</Badge>
                <div>
                  <h3>{title}</h3>
                  <p style={{ fontSize: 11, marginTop: 4, lineHeight: 1.8 }}>
                    {text}
                  </p>
                </div>
              </div>
            ))}
          </div>
        </Card>
      </div>
      <Card
        className="space-top"
        title="Backtest history"
        subtitle="Persisted research runs by strategy version"
        action={
          <Freshness
            updatedAt={runs.dataUpdatedAt}
            fetching={runs.isFetching}
          />
        }
      >
        {runs.isLoading ? (
          <Loading />
        ) : runs.error ? (
          <div className="panel-body">
            <ErrorState error={runs.error} retry={() => void runs.refetch()} />
          </div>
        ) : (
          <DataTable<Backtest>
            rows={runs.data ?? []}
            getKey={(b) => b.id}
            empty="No backtests have been recorded"
            columns={[
              {
                label: "Run",
                render: (b) => (
                  <button
                    className="subtle-link"
                    style={{ background: "none", border: 0, padding: 0 }}
                    onClick={() => setSelected(b)}
                  >
                    {String(b.id).slice(0, 8)}
                  </button>
                ),
              },
              {
                label: "Strategy",
                render: (b) =>
                  strategies.data?.find((s) => s.id === b.strategy_id)?.name ??
                  String(b.strategy_id).slice(0, 8),
              },
              { label: "Status", render: (b) => <Status value={b.status} /> },
              {
                label: "Version",
                render: (b) => (b.version ? `v${b.version}` : "—"),
              },
              {
                label: "Net P&L",
                render: (b) =>
                  format.money(
                    b.metrics?.net_pnl ??
                      asRecord(asRecord(b.result).metrics).net_profit,
                  ),
              },
              { label: "Created", render: (b) => format.date(b.created_at) },
              {
                label: "Report",
                render: (b) => (
                  <Button
                    variant="ghost"
                    className="btn-small"
                    onClick={() => setSelected(b)}
                  >
                    View results
                  </Button>
                ),
              },
            ]}
          />
        )}
      </Card>
      {selected && (
        <BacktestReport key={selected.id} id={selected.id} initial={selected} />
      )}
      <ResearchRuns />
    </>
  );
}
function BacktestReport({ id, initial }: { id: number; initial: Backtest }) {
  const query = useApi<Backtest>(`/backtests/${id}`, 0);
  const [tab, setTab] = useState("equity");
  const data = query.data ?? initial;
  const results = asRecord(data.results ?? data.result);
  const metrics = asRecord(data.metrics ?? results.metrics);
  const points = asArray<Point>(data.equity_curve ?? results.equity_curve);
  const trades = asArray<Record<string, unknown>>(
    data.trades ?? results.trades,
  );
  const limitations = [
    ...asArray<string>(data.limitations),
    ...asArray<string>(results.warnings),
    ...asArray<string>(results.assumptions),
  ];
  return (
    <div className="space-top">
      {query.error && <ErrorState error={query.error} />}
      <div className="grid-4">
        <Metric
          label="Net P&L"
          value={format.money(metrics.net_pnl ?? metrics.net_profit)}
          icon={<CircleDollarSign />}
          subtext="After modeled fees"
          trend={
            metrics.net_pnl !== undefined
              ? Number(metrics.net_pnl) >= 0
              : undefined
          }
        />
        <Metric
          label="Total trades"
          value={format.number(metrics.total_trades ?? metrics.trade_count, 0)}
          icon={<Activity />}
          subtext="Completed simulated trades"
        />
        <Metric
          label="Win rate"
          value={
            metrics.win_rate !== undefined
              ? `${format.number(Number(metrics.win_rate) * 100, 1)}%`
              : "—"
          }
          icon={<TrendingUp />}
          subtext="Recorded historical outcomes"
        />
        <Metric
          label="Max drawdown"
          value={
            metrics.max_drawdown_pct !== undefined
              ? `${format.number(metrics.max_drawdown_pct, 2)}%`
              : metrics.max_drawdown_fraction !== undefined
                ? `${format.number(Number(metrics.max_drawdown_fraction) * 100, 2)}%`
                : "—"
          }
          icon={<ArrowDownRight />}
          subtext="Historical equity peak to trough"
        />
      </div>
      <Card
        className="space-top"
        title={`Research report · ${String(id).slice(0, 8)}`}
        subtitle="Historical simulation · compare only runs with comparable assumptions"
        action={<Status value={data.status} />}
      >
        <div className="panel-body" style={{ paddingBottom: 0 }}>
          <div className="tabs">
            {["equity", "trades", "metrics", "provenance"].map((t) => (
              <Button
                key={t}
                variant="ghost"
                className={tab === t ? "selected" : ""}
                onClick={() => setTab(t)}
              >
                {t[0].toUpperCase() + t.slice(1)}
              </Button>
            ))}
          </div>
        </div>
        {tab === "equity" ? (
          <EquityChart points={points} />
        ) : tab === "trades" ? (
          <DataTable
            rows={trades}
            getKey={(t, i) => String(t.id ?? i)}
            empty="The strategy did not complete any trades over this sample"
            columns={[
              { label: "Side", render: (t) => String(t.side ?? "—") },
              { label: "Entry", render: (t) => format.money(t.entry_price) },
              { label: "Exit", render: (t) => format.money(t.exit_price) },
              {
                label: "Quantity",
                render: (t) => format.number(t.quantity, 0),
              },
              {
                label: "Net P&L",
                render: (t) => format.money(t.net_pnl ?? t.pnl),
              },
              {
                label: "Exit reason",
                render: (t) => String(t.exit_reason ?? "—"),
              },
            ]}
          />
        ) : (
          <div className="panel-body">
            <JsonView
              value={
                tab === "metrics"
                  ? metrics
                  : {
                      strategy_id: data.strategy_id,
                      strategy_version: data.version ?? data.strategy_version,
                      ...results,
                      equity_curve: undefined,
                      trades: undefined,
                    }
              }
            />
          </div>
        )}
      </Card>
      {limitations.length > 0 && (
        <Alert tone="amber">
          <strong>Model limitations</strong>
          <ul style={{ margin: "6px 0 0", paddingLeft: 18 }}>
            {limitations.map((l) => (
              <li key={l}>{l}</li>
            ))}
          </ul>
        </Alert>
      )}
      {data.error && <Alert tone="red">{data.error}</Alert>}
    </div>
  );
}
