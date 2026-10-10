"use client";
import { useState } from "react";
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
  Loading,
  Metric,
  PageTitle,
  Select,
} from "./ui";
import { EquityChart } from "./charts";
import type { Point } from "@/lib/types";
type Row = Record<string, unknown>;
export default function ReportsPage() {
  const [days, setDays] = useState("30");
  const analytics = useApi<Row>(`/reports/analytics?days=${days}`, 30000);
  const reports = useApi<Row[]>("/reports", 30000);
  const [period, setPeriod] = useState("DAILY");
  const [date, setDate] = useState("");
  const [selected, setSelected] = useState<Row | null>(null);
  const generate = useMutation({
    mutationFn: () =>
      api<Row>("/reports", { method: "POST", body: { period, date } }),
    onSuccess: (data) => {
      setSelected(data);
      void reports.refetch();
    },
  });
  const report = asRecord(selected?.payload ?? analytics.data?.report);
  const asset = String(report.currency ?? "USD");
  const warnings = asArray<string>(report.data_quality_warnings);
  return (
    <>
      <PageTitle
        eyebrow="PERSISTED PORTFOLIO EVIDENCE"
        title="Reports & analytics"
        description="Review account performance, strategy contributions, risk incidents and coverage. Completed reports retain their original period and data limitations."
        action={<Badge tone="blue">PAPER SIMULATION</Badge>}
      />
      <div className="grid-2">
        <Card
          title="Completed period report"
          subtitle="Periods follow Asia/Calcutta boundaries; incomplete periods cannot be finalized"
        >
          <div className="panel-body">
            {generate.error && (
              <Alert tone="red">{generate.error.message}</Alert>
            )}
            <form
              className="form-grid"
              onSubmit={(e) => {
                e.preventDefault();
                generate.mutate();
              }}
            >
              <Field label="Report period">
                <Select
                  value={period}
                  onChange={(e) => setPeriod(e.target.value)}
                >
                  <option>DAILY</option>
                  <option>WEEKLY</option>
                  <option>MONTHLY</option>
                </Select>
              </Field>
              <Field label="Date within completed period">
                <Input
                  required
                  type="date"
                  value={date}
                  onChange={(e) => setDate(e.target.value)}
                />
              </Field>
              <div className="form-actions form-full">
                <Button disabled={generate.isPending}>
                  {generate.isPending
                    ? "Generating…"
                    : "Generate completed report"}
                </Button>
              </div>
            </form>
          </div>
        </Card>
        <Card
          title="Rolling analytics"
          subtitle="Current account evidence can be incomplete"
        >
          <div className="panel-body">
            <Field label="Lookback days">
              <Select
                value={days}
                onChange={(e) => {
                  setDays(e.target.value);
                  setSelected(null);
                }}
              >
                <option value="7">7 days</option>
                <option value="30">30 days</option>
                <option value="90">90 days</option>
                <option value="365">365 days</option>
              </Select>
            </Field>
            <div className="form-actions">
              <Button
                variant="secondary"
                onClick={() => {
                  setSelected(null);
                  void analytics.refetch();
                }}
              >
                Show rolling account view
              </Button>
            </div>
          </div>
        </Card>
      </div>
      {analytics.isLoading ? (
        <Loading />
      ) : analytics.error ? (
        <ErrorState error={analytics.error} />
      ) : (
        <>
          <div className="grid-4 space-top">
            <Metric
              label="Net realized P&L"
              value={format.money(
                report.net_realized_pnl ??
                  report.net_realized_pnl_excluding_funding,
                asset,
              )}
              subtext={
                report.net_realized_pnl !== undefined
                  ? "After recorded fees and funding"
                  : "After recorded fees; funding excluded"
              }
            />
            <Metric
              label="Completed trades"
              value={format.number(report.total_trades, 0)}
              subtext={`${format.number(report.winning_trades, 0)} wins / ${format.number(report.losing_trades, 0)} losses`}
            />
            <Metric
              label="Maximum drawdown"
              value={
                report.maximum_drawdown == null
                  ? "—"
                  : `${format.number(Number(report.maximum_drawdown) * 100)}%`
              }
              subtext="From persisted equity snapshots"
            />
            <Metric
              label="Paid fees"
              value={format.money(report.fees, asset)}
              subtext="Funding shown only when evidenced"
            />
          </div>
          <Card
            className="space-top"
            title={
              selected
                ? `${selected.period} report #${selected.id}`
                : "Rolling equity"
            }
            subtitle={`${format.date(report.period_start)} — ${format.date(report.period_end)} IST`}
            action={
              selected && (
                <div className="button-group">
                  <a
                    className="btn btn-secondary"
                    href={`/api/v1/reports/${selected.id}/export?format=json`}
                    download
                  >
                    Export JSON
                  </a>
                  <a
                    className="btn btn-secondary"
                    href={`/api/v1/reports/${selected.id}/export?format=csv`}
                    download
                  >
                    Export CSV
                  </a>
                </div>
              )
            }
          >
            <EquityChart
              points={asArray<Point>(report.equity_curve)}
              asset={asset}
            />
          </Card>
          <div className="grid-2 space-top">
            <Card title="Strategy contributions">
              <DataTable
                rows={asArray<Row>(report.strategy_contributions)}
                getKey={(r) => String(r.strategy_id)}
                columns={[
                  { label: "Strategy", render: (r) => String(r.strategy_name) },
                  {
                    label: "Gross P&L",
                    render: (r) => format.money(r.gross_realized_pnl, asset),
                  },
                  { label: "Fees", render: (r) => format.money(r.fees, asset) },
                  {
                    label: "Net P&L",
                    render: (r) =>
                      format.money(r.net_realized_pnl_excluding_funding, asset),
                  },
                ]}
              />
            </Card>
            <Card title="Data coverage & limitations">
              <div className="panel-body">
                {warnings.map((w) => (
                  <Alert key={w} tone="amber">
                    {w}
                  </Alert>
                ))}
                {!warnings.length && (
                  <p className="list-note">
                    No report coverage warnings were returned.
                  </p>
                )}
              </div>
            </Card>
          </div>
          <Card className="space-top" title="Risk & operational incidents">
            <DataTable
              rows={asArray<Row>(report.risk_incidents)}
              getKey={(r) => String(r.id)}
              columns={[
                { label: "Recorded", render: (r) => format.date(r.created_at) },
                { label: "Action", render: (r) => String(r.action) },
                { label: "Actor", render: (r) => String(r.actor) },
                {
                  label: "Details",
                  render: (r) => <JsonView value={r.details} />,
                },
              ]}
            />
          </Card>
        </>
      )}
      <Card className="space-top" title="Stored report archive">
        {reports.error ? (
          <ErrorState error={reports.error} />
        ) : (
          <DataTable
            rows={reports.data ?? []}
            getKey={(r) => String(r.id)}
            columns={[
              { label: "Report", render: (r) => `#${r.id} ${r.period}` },
              {
                label: "Period start",
                render: (r) => format.date(r.period_start),
              },
              { label: "Created", render: (r) => format.date(r.created_at) },
              {
                label: "Open",
                render: (r) => (
                  <Button variant="secondary" onClick={() => setSelected(r)}>
                    Review report
                  </Button>
                ),
              },
            ]}
          />
        )}
      </Card>
    </>
  );
}
