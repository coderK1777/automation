"use client";
import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { api, asArray, asRecord, format, useApi } from "@/lib/api";
import type { Point } from "@/lib/types";
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
  Metric,
  Select,
} from "./ui";
import { EquityChart } from "./charts";
type Row = Record<string, unknown>;
export default function DemoReportsPanel({
  connected,
}: {
  connected: boolean;
}) {
  const reports = useApi<Row[]>("/demo/reports", 30000);
  const [period, setPeriod] = useState("DAILY");
  const [date, setDate] = useState("");
  const [selected, setSelected] = useState<Row | null>(null);
  const create = useMutation({
    mutationFn: () =>
      api<Row>("/demo/reports", { method: "POST", body: { period, date } }),
    onSuccess: (result) => {
      setSelected(result);
      void reports.refetch();
    },
  });
  const result = asRecord(selected?.result),
    asset = String(result.currency ?? "UNVERIFIED");
  return (
    <>
      <Card
        className="space-top"
        title="Demo periodic reports"
        subtitle="Native demo exchange cash, funding, fees and equity; independent from the virtual paper account"
      >
        <div className="panel-body">
          {!connected && (
            <Alert tone="amber">
              Connect an authenticated demo account before generating reports. A
              credential binding alone provides no private balance or
              performance evidence.
            </Alert>
          )}
          {create.error && <Alert tone="red">{create.error.message}</Alert>}
          <form
            className="form-grid"
            onSubmit={(e) => {
              e.preventDefault();
              create.mutate();
            }}
          >
            <Field label="Demo report period">
              <Select
                value={period}
                onChange={(e) => setPeriod(e.target.value)}
              >
                <option>DAILY</option>
                <option>WEEKLY</option>
                <option>MONTHLY</option>
              </Select>
            </Field>
            <Field label="Demo period date (Asia/Calcutta)">
              <Input
                required
                type="date"
                value={date}
                onChange={(e) => setDate(e.target.value)}
              />
            </Field>
            <div className="form-actions form-full">
              <Button disabled={!connected || create.isPending}>
                Generate demo report
              </Button>
            </div>
          </form>
        </div>
        {reports.error ? (
          <ErrorState error={reports.error} />
        ) : (
          <DataTable
            rows={reports.data ?? []}
            getKey={(r) => String(r.id)}
            columns={[
              { label: "Report", render: (r) => `#${r.id} ${r.period}` },
              {
                label: "Period begins",
                render: (r) => format.date(r.period_start),
              },
              {
                label: "Data hash",
                render: (r) => String(r.dataset_hash).slice(0, 16),
              },
              { label: "Created", render: (r) => format.date(r.created_at) },
              {
                label: "Open",
                render: (r) => (
                  <Button variant="secondary" onClick={() => setSelected(r)}>
                    Review demo report
                  </Button>
                ),
              },
            ]}
          />
        )}
      </Card>
      {selected && (
        <>
          <Card
            className="space-top"
            title={`${selected.period} demo report #${selected.id}`}
            subtitle={`Observed through ${format.date(result.observed_through)} IST`}
            action={
              <div className="button-group">
                <Badge tone={result.partial_period ? "amber" : "blue"}>
                  {result.partial_period ? "PARTIAL PERIOD" : "PERIOD REPORT"}
                </Badge>
                <a
                  className="btn btn-secondary"
                  href={`/api/v1/demo/reports/${selected.id}/export?format=json`}
                  download
                >
                  Demo JSON
                </a>
                <a
                  className="btn btn-secondary"
                  href={`/api/v1/demo/reports/${selected.id}/export?format=csv`}
                  download
                >
                  Demo CSV
                </a>
              </div>
            }
          >
            <EquityChart
              points={asArray<Point>(result.equity_curve)}
              asset={asset}
            />
          </Card>
          <div className="grid-4 space-top">
            <Metric
              label="Observed equity change"
              value={format.money(result.observed_equity_change, asset)}
              subtext="Within recorded coverage"
            />
            <Metric
              label="Closed trade P&L"
              value={format.money(
                result.closed_trade_net_pnl_excluding_funding,
                asset,
              )}
              subtext="Recorded fees included; funding separate"
            />
            <Metric
              label="Native funding cash flow"
              value={format.money(result.funding_cashflow, asset)}
              subtext="Actual attributed wallet movements"
            />
            <Metric
              label="Closed episodes"
              value={format.number(result.closed_episodes, 0)}
              subtext={`${format.number(result.wins, 0)} wins / ${format.number(result.losses, 0)} losses`}
            />
          </div>
          <Card className="space-top" title="Demo report coverage & evidence">
            <div className="panel-body">
              {asArray<string>(result.coverage).map((w) => (
                <Alert key={w} tone="amber">
                  {w}
                </Alert>
              ))}
              <p className="list-note">
                This report does not certify demo order acceptance or authorize
                production trading. Denominations remain separate.
              </p>
              <details>
                <summary>Native fees, cash flows and trade episodes</summary>
                <JsonView value={result} />
              </details>
            </div>
          </Card>
        </>
      )}
    </>
  );
}
