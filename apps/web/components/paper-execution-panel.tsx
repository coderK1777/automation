"use client";
import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api, asArray, asRecord, format, useApi } from "@/lib/api";
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
  Status,
  Textarea,
} from "./ui";
type Row = Record<string, unknown>;
export default function PaperExecutionPanel() {
  const client = useQueryClient();
  const account = useApi<Row>("/paper/account", 30000);
  const asset = String(asRecord(account.data).settlement_asset ?? "USD");
  const execution = useApi<Row>("/paper/execution", 10000);
  const history = useApi<Row>("/paper/account/history", 30000);
  const funding = useApi<Row[]>("/paper/funding", 30000);
  const [resetOpen, setResetOpen] = useState(false);
  const [balance, setBalance] = useState("");
  const [reason, setReason] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [symbol, setSymbol] = useState("BTCUSD");
  const [timestamp, setTimestamp] = useState("");
  const [rate, setRate] = useState("");
  const [mark, setMark] = useState("");
  const [source, setSource] = useState("");
  const cancel = useMutation({
    mutationFn: (id: unknown) =>
      api(`/paper/orders/${id}/cancel`, { method: "POST", body: {} }),
    onSuccess: () => void client.invalidateQueries(),
  });
  const reset = useMutation({
    mutationFn: () =>
      api("/paper/account/reset", {
        method: "POST",
        body: { confirm: true, balance, reason },
      }),
    onSuccess: () => {
      setResetOpen(false);
      setBalance("");
      setReason("");
      setConfirmation("");
      void client.invalidateQueries();
    },
  });
  const schedule = useMutation({
    mutationFn: () =>
      api("/paper/funding", {
        method: "POST",
        body: { symbol, timestamp, rate, mark_price: mark, source },
      }),
    onSuccess: () => {
      setTimestamp("");
      setRate("");
      setMark("");
      setSource("");
      void funding.refetch();
    },
  });
  return (
    <>
      <Card
        className="space-top"
        title="Pending simulated entries"
        subtitle="Only pending entry intents can be canceled; existing position protection remains active"
        action={<Badge tone="blue">PAPER MODEL</Badge>}
      >
        {cancel.error && <Alert tone="red">{cancel.error.message}</Alert>}
        {execution.error ? (
          <ErrorState error={execution.error} />
        ) : (
          <>
            <div className="panel-body">
              <div className="health-row">
                <span>Reserved entry margin</span>
                <strong>
                  {format.money(execution.data?.reserved_margin, asset)}
                </strong>
              </div>
              <div className="health-row">
                <span>Reserved entry risk</span>
                <strong>
                  {format.money(execution.data?.reserved_risk, asset)}
                </strong>
              </div>
            </div>
            <DataTable
              rows={asArray<Row>(execution.data?.pending)}
              getKey={(r) => String(r.id)}
              columns={[
                { label: "Instrument", render: (r) => String(r.symbol) },
                { label: "Type", render: (r) => String(r.order_type) },
                { label: "Quantity", render: (r) => String(r.quantity) },
                {
                  label: "State",
                  render: (r) => <Status value={String(r.status)} />,
                },
                {
                  label: "Action",
                  render: (r) => (
                    <Button
                      variant="secondary"
                      disabled={cancel.isPending}
                      onClick={() => cancel.mutate(r.id)}
                    >
                      Cancel entry
                    </Button>
                  ),
                },
              ]}
            />
            <div className="panel-body">
              <details>
                <summary>Simulation assumptions</summary>
                {asArray<string>(execution.data?.assumptions).map((v) => (
                  <p className="list-note" key={v}>
                    {v}
                  </p>
                ))}
              </details>
            </div>
          </>
        )}
      </Card>
      <div className="grid-2 space-top">
        <Card
          title="Virtual capital reset"
          subtitle="Preserves existing orders, fills, fees and the complete movement history"
        >
          <div className="panel-body">
            <p className="list-note">
              Stop all strategies, close open positions and cancel pending
              entries before changing simulated cash. The backend records the
              adjustment as a new capital movement.
            </p>
            <Button variant="secondary" onClick={() => setResetOpen(true)}>
              Review virtual balance reset
            </Button>
          </div>
        </Card>
        <Card title="Virtual capital movement history">
          {history.error ? (
            <ErrorState error={history.error} />
          ) : (
            <DataTable
              rows={asArray<Row>(history.data?.movements)}
              getKey={(r) => String(r.id)}
              columns={[
                { label: "Movement", render: (r) => String(r.kind) },
                {
                  label: "Amount",
                  render: (r) => format.money(r.amount, asset),
                },
                { label: "Recorded", render: (r) => format.date(r.created_at) },
                {
                  label: "Details",
                  render: (r) => <JsonView value={r.details} />,
                },
              ]}
            />
          )}
        </Card>
      </div>
      <Card
        className="space-top"
        title="Attributed simulated funding"
        subtitle="Only explicitly sourced rates scheduled before their effective time can affect paper positions"
      >
        <div className="panel-body">
          {schedule.error && <Alert tone="red">{schedule.error.message}</Alert>}
          {schedule.isSuccess && (
            <Alert tone="green">Attributed funding event scheduled.</Alert>
          )}
          <form
            className="form-grid"
            onSubmit={(e) => {
              e.preventDefault();
              schedule.mutate();
            }}
          >
            <Field label="Funding instrument">
              <Select
                value={symbol}
                onChange={(e) => setSymbol(e.target.value)}
              >
                <option>BTCUSD</option>
                <option>ETHUSD</option>
              </Select>
            </Field>
            <Field label="Future effective timestamp with timezone">
              <Input
                required
                placeholder="2026-10-10T00:00:00+05:30"
                value={timestamp}
                onChange={(e) => setTimestamp(e.target.value)}
              />
            </Field>
            <Field
              label="Funding rate (fraction)"
              hint="Positive pays from long to short; a raw ticker value is not sufficient evidence."
            >
              <Input
                required
                type="number"
                step="any"
                min="-0.999999"
                max="0.999999"
                value={rate}
                onChange={(e) => setRate(e.target.value)}
              />
            </Field>
            <Field label="Effective mark price">
              <Input
                required
                type="number"
                step="any"
                min="0.000001"
                value={mark}
                onChange={(e) => setMark(e.target.value)}
              />
            </Field>
            <div className="form-full">
              <Field label="Verified source reference">
                <Input
                  required
                  value={source}
                  onChange={(e) => setSource(e.target.value)}
                />
              </Field>
            </div>
            <div className="form-actions form-full">
              <Button disabled={schedule.isPending}>
                Schedule paper funding event
              </Button>
            </div>
          </form>
        </div>
        {funding.error ? (
          <ErrorState error={funding.error} />
        ) : (
          <DataTable
            rows={funding.data ?? []}
            getKey={(r) => String(r.id)}
            columns={[
              { label: "Instrument", render: (r) => String(r.symbol) },
              { label: "Effective", render: (r) => format.date(r.timestamp) },
              { label: "Rate fraction", render: (r) => String(r.rate) },
              { label: "Mark", render: (r) => format.money(r.mark_price) },
              { label: "Source", render: (r) => String(r.source) },
            ]}
          />
        )}
      </Card>
      <Dialog
        open={resetOpen}
        onOpenChange={(open) => {
          setResetOpen(open);
          if (!open) setConfirmation("");
        }}
        title="Reset virtual paper capital?"
        description="This changes simulated cash and records a capital adjustment. The existing trading and equity history is preserved."
      >
        {reset.error && <Alert tone="red">{reset.error.message}</Alert>}
        <div className="stack">
          <Field label={`New virtual balance (${asset})`}>
            <Input
              type="number"
              min="1"
              step="any"
              required
              value={balance}
              onChange={(e) => setBalance(e.target.value)}
            />
          </Field>
          <Field label="Reason for adjustment">
            <Textarea
              required
              maxLength={500}
              value={reason}
              onChange={(e) => setReason(e.target.value)}
            />
          </Field>
          <Field label="Type RESET PAPER to confirm">
            <Input
              value={confirmation}
              onChange={(e) => setConfirmation(e.target.value)}
              autoComplete="off"
            />
          </Field>
          <Button
            disabled={
              reset.isPending ||
              confirmation !== "RESET PAPER" ||
              !balance ||
              !reason.trim()
            }
            onClick={() => reset.mutate()}
          >
            Confirm virtual balance reset
          </Button>
        </div>
      </Dialog>
    </>
  );
}
