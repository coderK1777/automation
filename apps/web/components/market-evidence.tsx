"use client";
import { useEffect, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { api, asArray, asRecord, format, useApi } from "@/lib/api";
import {
  Alert,
  Badge,
  Button,
  Card,
  DataTable,
  ErrorState,
  Field,
  Input,
  JsonView,
  Select,
  Status,
} from "./ui";
type Row = Record<string, unknown>;
export function MarketStream() {
  const snapshot = useApi<Row>("/market/snapshot", 10000);
  const [live, setLive] = useState<Row | null>(null);
  const [connected, setConnected] = useState(false);
  const [clock, setClock] = useState(0);
  useEffect(() => {
    const stream = new EventSource("/api/v1/market/stream");
    stream.addEventListener("snapshot", (event) => {
      try {
        const value: unknown = JSON.parse((event as MessageEvent).data);
        const record = asRecord(value);
        if (Array.isArray(record.feeds)) {
          setLive(record);
          setConnected(true);
        }
      } catch {
        setConnected(false);
      }
    });
    stream.onopen = () => setConnected(true);
    stream.onerror = () => setConnected(false);
    const timer = window.setInterval(() => setClock(Date.now()), 10000);
    return () => {
      window.clearInterval(timer);
      stream.close();
    };
  }, []);
  const rows = asArray<Row>(
    (connected ? live : snapshot.data)?.feeds ?? snapshot.data?.feeds,
  );
  return (
    <Card
      className="space-top"
      title="Central market feed"
      subtitle="Public exchange events shared by workers and browsers; stale or recovering state remains visible"
      action={
        <Badge tone={connected ? "green" : "amber"}>
          {connected ? "SSE CONNECTED" : "REST RECOVERY"}
        </Badge>
      }
    >
      {snapshot.error && !rows.length ? (
        <ErrorState error={snapshot.error} />
      ) : (
        <DataTable
          rows={rows}
          getKey={(r) => `${r.symbol}:${r.channel}`}
          empty="No public feed observation has been recorded"
          columns={[
            { label: "Instrument", render: (r) => String(r.symbol) },
            { label: "Feed", render: (r) => String(r.channel) },
            {
              label: "State",
              render: (r) => {
                const received = new Date(String(r.received_at)).getTime();
                const old =
                  Number.isFinite(received) &&
                  (clock || Date.now()) - received > 35000;
                return <Status value={old ? "STALE" : String(r.status)} />;
              },
            },
            {
              label: "Last",
              render: (r) => format.number(asRecord(r.payload).last_price),
            },
            {
              label: "Mark",
              render: (r) => format.number(asRecord(r.payload).mark_price),
            },
            {
              label: "Funding (raw exchange units)",
              render: (r) =>
                asRecord(r.payload).funding_rate == null
                  ? "—"
                  : String(asRecord(r.payload).funding_rate),
            },
            {
              label: "Bid / Ask",
              render: (r) =>
                `${format.number(asRecord(r.payload).bid)} / ${format.number(asRecord(r.payload).ask)}`,
            },
            { label: "Received", render: (r) => format.date(r.received_at) },
            { label: "Gaps", render: (r) => String(r.gap_count ?? 0) },
          ]}
        />
      )}
    </Card>
  );
}
export function DatasetPanel() {
  const datasets = useApi<Row[]>("/market/datasets", 30000);
  const gaps = useApi<Row[]>("/market/gaps", 30000);
  const [symbol, setSymbol] = useState("BTCUSD");
  const [timeframe, setTimeframe] = useState("60");
  const [limit, setLimit] = useState("1500");
  const [selected, setSelected] = useState<Row | null>(null);
  const create = useMutation({
    mutationFn: () =>
      api<Row>("/market/datasets", {
        method: "POST",
        body: {
          symbol,
          timeframe_seconds: Number(timeframe),
          limit: Number(limit),
        },
      }),
    onSuccess: (data) => {
      setSelected(data);
      void datasets.refetch();
      void gaps.refetch();
    },
  });
  return (
    <>
      <Card
        className="space-top"
        title="Immutable research datasets"
        subtitle="Freeze stored candles and inspect completeness, source and content hash before research"
      >
        <div className="panel-body">
          {create.error && <Alert tone="red">{create.error.message}</Alert>}
          <form
            className="form-grid"
            onSubmit={(e) => {
              e.preventDefault();
              create.mutate();
            }}
          >
            <Field label="Dataset instrument">
              <Select
                value={symbol}
                onChange={(e) => setSymbol(e.target.value)}
              >
                <option>BTCUSD</option>
                <option>ETHUSD</option>
              </Select>
            </Field>
            <Field label="Dataset timeframe">
              <Select
                value={timeframe}
                onChange={(e) => setTimeframe(e.target.value)}
              >
                <option value="60">1 minute</option>
                <option value="300">5 minutes</option>
                <option value="900">15 minutes</option>
                <option value="3600">1 hour</option>
                <option value="14400">4 hours</option>
              </Select>
            </Field>
            <Field label="Stored candle count">
              <Input
                required
                type="number"
                min="1"
                max="100000"
                value={limit}
                onChange={(e) => setLimit(e.target.value)}
              />
            </Field>
            <div className="form-actions">
              <Button disabled={create.isPending}>Freeze dataset</Button>
            </div>
          </form>
        </div>
        {datasets.error ? (
          <ErrorState error={datasets.error} />
        ) : (
          <DataTable
            rows={datasets.data ?? []}
            getKey={(r) => String(r.id)}
            columns={[
              { label: "Dataset", render: (r) => `#${r.id} ${r.symbol}` },
              {
                label: "Interval",
                render: (r) => `${Number(r.timeframe_seconds) / 60} min`,
              },
              { label: "Candles", render: (r) => String(r.candle_count) },
              {
                label: "Coverage",
                render: (r) => (
                  <Badge tone={r.complete ? "green" : "amber"}>
                    {r.complete ? "COMPLETE" : "HAS GAPS"}
                  </Badge>
                ),
              },
              {
                label: "Content hash",
                render: (r) => String(r.content_hash).slice(0, 16),
              },
              {
                label: "Review",
                render: (r) => (
                  <Button variant="secondary" onClick={() => setSelected(r)}>
                    Inspect dataset
                  </Button>
                ),
              },
            ]}
          />
        )}
        {selected && (
          <div className="panel-body">
            <h3>Dataset provenance</h3>
            <JsonView value={selected} />
            <a
              className="btn btn-secondary"
              href={`/api/v1/market/datasets/${selected.id}/candles`}
              target="_blank"
              rel="noreferrer"
            >
              View immutable candle data
            </a>
          </div>
        )}
      </Card>
      <Card
        className="space-top"
        title="Market data gaps"
        subtitle="Closed-candle continuity is required for strategy evaluation"
      >
        {gaps.error ? (
          <ErrorState error={gaps.error} />
        ) : (
          <DataTable
            rows={gaps.data ?? []}
            getKey={(r, i) => String(r.id ?? i)}
            columns={[
              { label: "Instrument", render: (r) => String(r.symbol) },
              {
                label: "Timeframe",
                render: (r) => `${Number(r.timeframe_seconds) / 60} min`,
              },
              { label: "Start", render: (r) => format.date(r.start_at) },
              { label: "End", render: (r) => format.date(r.end_at) },
              {
                label: "State",
                render: (r) => <Status value={String(r.status)} />,
              },
            ]}
          />
        )}
      </Card>
    </>
  );
}
