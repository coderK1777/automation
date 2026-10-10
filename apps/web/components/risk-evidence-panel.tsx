"use client";
import { useState } from "react";
import { asRecord, format, useApi } from "@/lib/api";
import {
  Button,
  Card,
  DataTable,
  Dialog,
  ErrorState,
  JsonView,
  Loading,
  Status,
} from "./ui";
type Row = Record<string, unknown>;
export default function RiskEvidencePanel() {
  const decisions = useApi<Row[]>("/risk/decisions?limit=100", 15000);
  const signals = useApi<Row[]>("/signals?limit=100", 15000);
  const [selected, setSelected] = useState<Row | null>(null);
  return (
    <>
      <Card
        className="space-top"
        title="Recorded paper risk decisions"
        subtitle="Persisted backend decisions, including rejected entries and the limits evaluated at that time"
      >
        {decisions.isLoading ? (
          <Loading />
        ) : decisions.error ? (
          <ErrorState error={decisions.error} />
        ) : (
          <DataTable
            rows={decisions.data ?? []}
            getKey={(row) => String(row.id)}
            columns={[
              {
                label: "Observed",
                render: (row) => format.date(row.created_at),
              },
              {
                label: "Strategy",
                render: (row) => `#${row.strategy_id} v${row.version}`,
              },
              { label: "Stage", render: (row) => String(row.stage) },
              {
                label: "Decision",
                render: (row) => (
                  <Status value={row.approved ? "APPROVED" : "REJECTED"} />
                ),
              },
              {
                label: "Reasons",
                render: (row) =>
                  JSON.stringify(asRecord(row.decision).reasons ?? []),
              },
              {
                label: "Evidence",
                render: (row) => (
                  <Button variant="secondary" onClick={() => setSelected(row)}>
                    Review risk decision
                  </Button>
                ),
              },
            ]}
          />
        )}
      </Card>
      <Card
        className="space-top"
        title="Recorded paper signals"
        subtitle="Actual strategy signals from closed candles; an emitted signal does not guarantee order acceptance"
      >
        {signals.isLoading ? (
          <Loading />
        ) : signals.error ? (
          <ErrorState error={signals.error} />
        ) : (
          <DataTable
            rows={signals.data ?? []}
            getKey={(row) => String(row.id)}
            columns={[
              {
                label: "Closed candle",
                render: (row) => format.date(row.candle_time),
              },
              {
                label: "Strategy",
                render: (row) => `#${row.strategy_id} v${row.version}`,
              },
              { label: "Instrument", render: (row) => String(row.symbol) },
              { label: "Side", render: (row) => String(row.side) },
              {
                label: "Status",
                render: (row) => <Status value={String(row.status)} />,
              },
              { label: "Reason", render: (row) => String(row.reason ?? "—") },
            ]}
          />
        )}
      </Card>
      <Dialog
        open={selected !== null}
        onOpenChange={(open) => !open && setSelected(null)}
        title="Recorded paper risk evidence"
        description="Requested, sized and approved quantities, risk, margin, failed gates and exact limits are retained as backend evidence."
      >
        <JsonView value={selected} />
      </Dialog>
    </>
  );
}
