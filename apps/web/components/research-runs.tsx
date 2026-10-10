"use client";
import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { api, asRecord, format, useApi } from "@/lib/api";
import type { Strategy } from "@/lib/types";
import {
  Alert,
  Badge,
  Button,
  Card,
  DataTable,
  Dialog,
  ErrorState,
  Field,
  Input,
  JsonView,
  Select,
  Textarea,
} from "./ui";
type Row = Record<string, unknown>;
const defaults: Record<string, Row> = {
  OUT_OF_SAMPLE: { split_index: 1000, warmup_bars: 100 },
  WALK_FORWARD: {
    training_bars: 1000,
    testing_bars: 300,
    warmup_bars: 100,
    minimum_training_trades: 3,
  },
  STABILITY: {
    parameters: { fast_period: [5, 9, 13], slow_period: [18, 21, 30] },
  },
  COST_STRESS: { multipliers: [1, 2, 3] },
  MONTE_CARLO: { seed: 42, simulations: 500, mode: "BOOTSTRAP" },
  BENCHMARK: {},
  REGIME: {},
  PORTFOLIO: { symbols: ["BTCUSD", "ETHUSD"] },
};
export default function ResearchRuns() {
  const strategies = useApi<Strategy[]>("/strategies", 30000);
  const datasets = useApi<Row[]>("/market/datasets", 30000);
  const runs = useApi<Row[]>("/research/runs", 30000);
  const [strategy, setStrategy] = useState("");
  const [kind, setKind] = useState("OUT_OF_SAMPLE");
  const [dataset, setDataset] = useState("");
  const [capital, setCapital] = useState("10000");
  const [limit, setLimit] = useState("2000");
  const [parameters, setParameters] = useState(
    JSON.stringify(defaults.OUT_OF_SAMPLE, null, 2),
  );
  const [error, setError] = useState("");
  const [selected, setSelected] = useState<Row | null>(null);
  const run = useMutation({
    mutationFn: (body: Row) =>
      api<Row>("/research/runs", { method: "POST", body }),
    onSuccess: (data) => {
      setSelected(data);
      void runs.refetch();
    },
  });
  const submit = () => {
    try {
      const value: unknown = JSON.parse(parameters);
      if (!value || typeof value !== "object" || Array.isArray(value))
        throw new Error("Research parameters must be a JSON object.");
      setError("");
      run.mutate({
        strategy_id: Number(strategy),
        kind,
        initial_capital: capital,
        limit: Number(limit),
        dataset_id: dataset || undefined,
        parameters: value,
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Invalid parameters");
    }
  };
  const chosen = strategies.data?.find((v) => String(v.id) === strategy);
  const compatible = datasets.data?.filter(
    (v) =>
      !chosen ||
      (v.symbol === chosen.config.symbol &&
        Number(v.timeframe_seconds) === chosen.config.timeframe_seconds),
  );
  return (
    <>
      <Card
        className="space-top"
        title="Robustness research"
        subtitle="Out-of-sample windows, parameter stability, cost stress, seeded Monte Carlo, benchmarks, regime and portfolio evidence"
      >
        <div className="panel-body">
          <Alert tone="blue">
            Research runs retain configuration and dataset hashes. Training
            selection cannot use test-window outcomes. Monte Carlo resamples
            recorded trades; it does not forecast market prices.
          </Alert>
          {strategies.error && <ErrorState error={strategies.error} />}
          {error && <Alert tone="red">{error}</Alert>}
          {run.error && <Alert tone="red">{run.error.message}</Alert>}
          <form
            className="form-grid"
            onSubmit={(e) => {
              e.preventDefault();
              submit();
            }}
          >
            <Field label="Research strategy">
              <Select
                required
                value={strategy}
                onChange={(e) => {
                  setStrategy(e.target.value);
                  setDataset("");
                }}
              >
                <option value="">Choose a strategy</option>
                {strategies.data?.map((v) => (
                  <option key={v.id} value={v.id}>
                    {v.name} · v{v.version}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Research method">
              <Select
                value={kind}
                onChange={(e) => {
                  setKind(e.target.value);
                  setParameters(
                    JSON.stringify(defaults[e.target.value], null, 2),
                  );
                }}
              >
                {Object.keys(defaults).map((v) => (
                  <option key={v} value={v}>
                    {v.replaceAll("_", " ")}
                  </option>
                ))}
              </Select>
            </Field>
            <Field
              label="Immutable dataset"
              hint="If unselected, the backend snapshots stored candles for this run."
            >
              <Select
                value={dataset}
                onChange={(e) => setDataset(e.target.value)}
              >
                <option value="">Snapshot currently stored candles</option>
                {compatible?.map((v) => (
                  <option key={String(v.id)} value={String(v.id)}>
                    #{String(v.id)} · {String(v.candle_count)} bars ·{" "}
                    {v.complete ? "complete" : "gaps"}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Simulated initial capital (USD)">
              <Input
                type="number"
                required
                min="1"
                step="any"
                value={capital}
                onChange={(e) => setCapital(e.target.value)}
              />
            </Field>
            <Field label="Maximum historical bars">
              <Input
                type="number"
                required
                min="100"
                max="5000"
                value={limit}
                onChange={(e) => setLimit(e.target.value)}
              />
            </Field>
            <div className="form-full">
              <Field label="Method parameters">
                <Textarea
                  className="code-input"
                  rows={8}
                  value={parameters}
                  onChange={(e) => setParameters(e.target.value)}
                />
              </Field>
            </div>
            <div className="form-actions form-full">
              <Button disabled={run.isPending || !strategy}>
                {run.isPending
                  ? "Running research…"
                  : "Run robustness research"}
              </Button>
            </div>
          </form>
        </div>
      </Card>
      <Card className="space-top" title="Research evidence archive">
        {runs.error ? (
          <ErrorState error={runs.error} />
        ) : (
          <DataTable
            rows={runs.data ?? []}
            getKey={(r) => String(r.id)}
            columns={[
              { label: "Run", render: (r) => `#${r.id}` },
              {
                label: "Method",
                render: (r) => (
                  <Badge tone="purple">
                    {String(r.kind).replaceAll("_", " ")}
                  </Badge>
                ),
              },
              {
                label: "Version",
                render: (r) => `Strategy ${r.strategy_id} · v${r.version}`,
              },
              { label: "Recorded", render: (r) => format.date(r.created_at) },
              {
                label: "Review",
                render: (r) => (
                  <Button variant="secondary" onClick={() => setSelected(r)}>
                    Review evidence
                  </Button>
                ),
              },
            ]}
          />
        )}
      </Card>
      <Dialog
        open={selected !== null}
        onOpenChange={(open) => {
          if (!open) setSelected(null);
        }}
        title={`${selected?.kind ?? "Research"} evidence #${selected?.id ?? ""}`}
        description="Review assumptions, warnings, data identity and independently computed outcomes before using this research."
      >
        <JsonView value={asRecord(selected?.result)} />
        <details>
          <summary>Reproducibility inputs</summary>
          <JsonView
            value={selected ? { ...selected, result: undefined } : {}}
          />
        </details>
        {selected && (
          <a
            href={`/api/v1/research/runs/${selected.id}`}
            target="_blank"
            rel="noreferrer"
            className="btn btn-secondary"
          >
            View source result
          </a>
        )}
      </Dialog>
    </>
  );
}
