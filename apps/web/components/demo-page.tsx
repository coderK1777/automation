"use client";
import { useState } from "react";
import Link from "next/link";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api, asArray, asRecord, format, useApi } from "@/lib/api";
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
  Loading,
  Metric,
  PageTitle,
  Select,
  Status,
  Textarea,
} from "./ui";
import { Reauthentication } from "./security-panel";
import DemoReportsPanel from "./demo-reports-panel";
type Row = Record<string, unknown>;
export default function DemoPage() {
  const client = useQueryClient();
  const status = useApi<Row>("/demo/status", 10000);
  const products = useApi<Row[]>("/demo/products", 30000);
  const orders = useApi<Row[]>("/demo/orders", 10000);
  const positions = useApi<Row[]>("/demo/positions", 10000);
  const runners = useApi<Row[]>("/demo/strategies", 15000);
  const fills = useApi<Row[]>("/demo/fills", 15000);
  const events = useApi<Row[]>("/demo/events", 15000);
  const movements = useApi<Row[]>("/demo/cash-movements", 15000);
  const risk = useApi<Row>("/demo/risk", 30000);
  const strategies = useApi<Strategy[]>("/strategies", 30000);
  const [selectedProduct, setSelectedProduct] = useState<Row | null>(null);
  const [minimum, setMinimum] = useState("1");
  const [step, setStep] = useState("1");
  const [maxQuantity, setMaxQuantity] = useState("");
  const [margin, setMargin] = useState("0.5");
  const [evidence, setEvidence] = useState("");
  const [symbol, setSymbol] = useState("BTCUSD");
  const [side, setSide] = useState("BUY");
  const [quantity, setQuantity] = useState("1");
  const [orderType, setOrderType] = useState("MARKET");
  const [limitPrice, setLimitPrice] = useState("");
  const [stopPrice, setStopPrice] = useState("");
  const [targetPrice, setTargetPrice] = useState("");
  const [confirmation, setConfirmation] = useState<{
    title: string;
    description: string;
    path: string;
    body?: unknown;
  } | null>(null);
  const [lastResult, setLastResult] = useState<Row | null>(null);
  const [riskText, setRiskText] = useState<string | null>(null);
  const [formError, setFormError] = useState("");
  const action = useMutation({
    mutationFn: (request: { path: string; method?: string; body?: unknown }) =>
      api<Row>(request.path, {
        method: request.method ?? "POST",
        body: request.body ?? {},
      }),
    onSuccess: (result) => {
      setLastResult(result);
      setConfirmation(null);
      setSelectedProduct(null);
      void client.invalidateQueries();
    },
  });
  const d = status.data ?? {},
    account = asRecord(d.account),
    worker = asRecord(d.worker);
  const configured = d.credential_configured === true;
  const connected = account.connected === true;
  const enabled =
    account.entries_enabled === true &&
    account.emergency_stop !== true &&
    account.reconciliation_required !== true;
  const newOrder = () => {
    setFormError("");
    if (
      !Number.isInteger(Number(quantity)) ||
      Number(quantity) <= 0 ||
      Number(stopPrice) <= 0 ||
      Number(targetPrice) <= 0 ||
      (orderType === "LIMIT" && Number(limitPrice) <= 0)
    ) {
      setFormError(
        "Enter a positive whole contract quantity and positive stop, target and limit prices.",
      );
      return;
    }
    setConfirmation({
      title: "Submit this demo order?",
      description: `${side} ${quantity} ${symbol} contracts in the Delta India demo account. The backend verifies margin, protection and independent risk before the worker submits it.`,
      path: "/demo/orders",
      body: {
        idempotency_key: crypto.randomUUID(),
        symbol,
        side,
        quantity: Number(quantity),
        order_type: orderType,
        limit_price: orderType === "LIMIT" ? limitPrice : undefined,
        stop_price: stopPrice,
        target_price: targetPrice,
      },
    });
  };
  return (
    <>
      <PageTitle
        eyebrow="DELTA INDIA TESTNET"
        title="Delta India demo"
        description="Dedicated demo account, order queue and reconciliation. Private account balances appear only after authenticated exchange responses."
        action={
          <Badge tone={enabled ? "green" : "amber"}>
            {enabled ? "DEMO ENTRY ENABLED" : "DEMO ENTRY BLOCKED"}
          </Badge>
        }
      />
      {status.isLoading ? (
        <Loading />
      ) : status.error ? (
        <ErrorState error={status.error} retry={() => void status.refetch()} />
      ) : (
        <>
          {!configured && (
            <Alert tone="amber">
              Demo API credentials are required. Configure separate demo
              credentials in protected server settings, then verify identity and
              connect.{" "}
              <Link href="/settings" className="subtle-link">
                Security settings →
              </Link>
            </Alert>
          )}
          {d.local_execution_authorized !== true && (
            <Alert tone="amber">
              The server has not authorized demo order submission. Read-only
              connection and risk review are separate from entry authorization.
            </Alert>
          )}
          {account.reconciliation_required === true && (
            <Alert tone="red">
              Reconciliation is required. New exposure is blocked. Review the
              issues below and reconcile against the exchange.
            </Alert>
          )}
          {action.error && <Alert tone="red">{action.error.message}</Alert>}
          {lastResult && (
            <Alert tone="green">
              The server recorded your action. Order submissions remain subject
              to the dedicated worker and exchange acknowledgement.
            </Alert>
          )}
          <div className="grid-4">
            <Metric
              label="Connection"
              value={
                <Status value={connected ? "CONNECTED" : "DISCONNECTED"} />
              }
              subtext={
                configured
                  ? "Demo credential binding configured"
                  : "Credentials missing"
              }
            />
            <Metric
              label="Demo equity"
              value={
                connected
                  ? format.money(
                      account.equity,
                      String(account.settlement_asset ?? "USD"),
                    )
                  : "—"
              }
              subtext="Authenticated exchange account"
            />
            <Metric
              label="Available margin"
              value={
                connected
                  ? format.money(
                      account.available_margin,
                      String(account.settlement_asset ?? "USD"),
                    )
                  : "—"
              }
              subtext="Reported by the demo exchange"
            />
            <Metric
              label="Demo executor"
              value={<Status value={String(worker.status ?? "UNKNOWN")} />}
              subtext={format.date(worker.updated_at)}
            />
          </div>
          <div className="grid-2 space-top">
            <Card
              title="Connection & entry authorization"
              subtitle="Identity verification, reconciled state and verified contract economics are enforced independently"
            >
              <div className="panel-body">
                <div className="button-group">
                  <Button
                    disabled={!configured || action.isPending}
                    onClick={() => action.mutate({ path: "/demo/connect" })}
                  >
                    Connect read-only
                  </Button>
                  <Button
                    variant="secondary"
                    disabled={!connected || action.isPending}
                    onClick={() => action.mutate({ path: "/demo/reconcile" })}
                  >
                    Reconcile account
                  </Button>
                  <Button
                    variant="secondary"
                    disabled={action.isPending}
                    onClick={() => action.mutate({ path: "/demo/disable" })}
                  >
                    Block new entries
                  </Button>
                  <Button
                    disabled={
                      !connected ||
                      d.local_execution_authorized !== true ||
                      action.isPending
                    }
                    onClick={() =>
                      setConfirmation({
                        title: "Enable demo entry eligibility?",
                        description:
                          "This allows separately approved demo strategies and manual orders to reach independent risk checks. It does not authorize production trading.",
                        path: "/demo/enable",
                        body: { confirm: true },
                      })
                    }
                  >
                    Enable demo entries
                  </Button>
                </div>
                <div className="health-row">
                  <span>Last reconciliation</span>
                  <strong>{format.date(account.last_reconciled_at)}</strong>
                </div>
                {asArray<unknown>(account.issues).map((issue, i) => (
                  <Alert key={i} tone="amber">
                    {typeof issue === "string" ? issue : JSON.stringify(issue)}
                  </Alert>
                ))}
                <p className="list-note">
                  Private validation:{" "}
                  {String(d.actual_private_validation ?? "NOT RECORDED")}. Order
                  acceptance:{" "}
                  {String(d.actual_order_validation ?? "NOT CERTIFIED")}.
                  Production execution remains disabled.
                </p>
              </div>
            </Card>
            <Reauthentication />
          </div>
        </>
      )}
      <Card
        className="space-top"
        title="Contract economics"
        subtitle="Record verified exchange minimum quantity, increments and margin with an attributable evidence reference"
        action={
          <Button
            variant="secondary"
            disabled={action.isPending}
            onClick={() => action.mutate({ path: "/demo/products/refresh" })}
          >
            Refresh public demo contracts
          </Button>
        }
      >
        {products.error ? (
          <ErrorState error={products.error} />
        ) : (
          <DataTable
            rows={products.data ?? []}
            getKey={(r) => String(r.id)}
            columns={[
              { label: "Instrument", render: (r) => String(r.symbol) },
              { label: "Product", render: (r) => String(r.id) },
              {
                label: "Verification",
                render: (r) => (
                  <Badge tone={r.verified_contract ? "green" : "amber"}>
                    {r.verified_contract ? "VERIFIED" : "REQUIRES EVIDENCE"}
                  </Badge>
                ),
              },
              { label: "Evidence", render: (r) => String(r.evidence ?? "—") },
              {
                label: "Review",
                render: (r) => (
                  <Button
                    variant="secondary"
                    onClick={() => {
                      setSelectedProduct(r);
                      const v = asRecord(r.verified_contract);
                      setMinimum(String(v.min_quantity ?? ""));
                      setStep(String(v.quantity_step ?? ""));
                      setMargin(String(v.initial_margin_rate ?? ""));
                      setMaxQuantity(String(v.max_quantity ?? ""));
                      setEvidence(String(r.evidence ?? ""));
                    }}
                  >
                    Review contract
                  </Button>
                ),
              },
            ]}
          />
        )}
      </Card>
      <div className="grid-2 space-top">
        <Card
          title="Protected demo entry"
          subtitle="A real demo exchange action; queued only after explicit confirmation"
        >
          <div className="panel-body">
            {formError && <Alert tone="red">{formError}</Alert>}
            <form
              className="form-grid"
              onSubmit={(e) => {
                e.preventDefault();
                newOrder();
              }}
            >
              <Field label="Symbol">
                <Select
                  value={symbol}
                  onChange={(e) => setSymbol(e.target.value)}
                >
                  <option>BTCUSD</option>
                  <option>ETHUSD</option>
                </Select>
              </Field>
              <Field label="Side">
                <Select value={side} onChange={(e) => setSide(e.target.value)}>
                  <option value="BUY">Buy / Long</option>
                  <option value="SELL">Sell / Short</option>
                </Select>
              </Field>
              <Field label="Whole contracts">
                <Input
                  required
                  type="number"
                  min="1"
                  step="1"
                  value={quantity}
                  onChange={(e) => setQuantity(e.target.value)}
                />
              </Field>
              <Field label="Order type">
                <Select
                  value={orderType}
                  onChange={(e) => setOrderType(e.target.value)}
                >
                  <option>MARKET</option>
                  <option>LIMIT</option>
                </Select>
              </Field>
              {orderType === "LIMIT" && (
                <Field label="Limit price">
                  <Input
                    required
                    type="number"
                    step="any"
                    min="0.000001"
                    value={limitPrice}
                    onChange={(e) => setLimitPrice(e.target.value)}
                  />
                </Field>
              )}
              <Field label="Protective stop price">
                <Input
                  required
                  type="number"
                  step="any"
                  min="0.000001"
                  value={stopPrice}
                  onChange={(e) => setStopPrice(e.target.value)}
                />
              </Field>
              <Field label="Profit target price">
                <Input
                  required
                  type="number"
                  step="any"
                  min="0.000001"
                  value={targetPrice}
                  onChange={(e) => setTargetPrice(e.target.value)}
                />
              </Field>
              <div className="form-actions form-full">
                <Button disabled={!enabled || !connected || action.isPending}>
                  Review demo order
                </Button>
              </div>
            </form>
          </div>
        </Card>
        <Card
          title="Demo portfolio limits"
          subtitle="Review the entire validated risk schema before saving"
        >
          <div className="panel-body">
            {risk.error ? (
              <ErrorState error={risk.error} />
            ) : (
              <>
                <Field label="Risk limits">
                  <Textarea
                    className="code-input"
                    value={riskText ?? JSON.stringify(risk.data ?? {}, null, 2)}
                    onChange={(e) => setRiskText(e.target.value)}
                  />
                </Field>
                <div className="form-actions">
                  <Button
                    disabled={!risk.data || action.isPending}
                    onClick={() => {
                      try {
                        const body: unknown = JSON.parse(
                          riskText ?? JSON.stringify(risk.data),
                        );
                        setFormError("");
                        action.mutate({
                          path: "/demo/risk",
                          method: "PUT",
                          body,
                        });
                      } catch {
                        setFormError("Risk limits must be valid JSON.");
                      }
                    }}
                  >
                    Save demo limits
                  </Button>
                </div>
              </>
            )}
          </div>
        </Card>
      </div>
      <Card
        className="space-top"
        title="Strategy deployment"
        subtitle="Current strategy version and backtest evidence are checked by the backend"
      >
        <DataTable
          rows={strategies.data ?? []}
          getKey={(r) => r.id}
          columns={[
            { label: "Strategy", render: (r) => r.name },
            { label: "Version", render: (r) => `v${r.version}` },
            {
              label: "Demo state",
              render: (r) => (
                <Status
                  value={String(
                    runners.data?.find((v) => v.strategy_id === r.id)?.status ??
                      "NOT DEPLOYED",
                  )}
                />
              ),
            },
            {
              label: "Demo control",
              render: (r) => (
                <div className="button-group">
                  <Button
                    disabled={!enabled || action.isPending}
                    onClick={() =>
                      setConfirmation({
                        title: `Start ${r.name} in DEMO?`,
                        description:
                          "The dedicated demo worker will evaluate this saved strategy against market data and submit only entries permitted by demo risk controls.",
                        path: `/demo/strategies/${r.id}/start`,
                        body: { confirm: true },
                      })
                    }
                  >
                    Start demo
                  </Button>
                  <Button
                    variant="secondary"
                    disabled={action.isPending}
                    onClick={() =>
                      action.mutate({ path: `/demo/strategies/${r.id}/pause` })
                    }
                  >
                    Pause demo
                  </Button>
                </div>
              ),
            },
          ]}
        />
      </Card>
      <Card className="space-top" title="Demo positions">
        {positions.error ? (
          <ErrorState error={positions.error} />
        ) : (
          <DataTable
            rows={positions.data ?? []}
            getKey={(r) => String(r.product_id ?? r.id)}
            columns={[
              {
                label: "Product",
                render: (r) => String(r.symbol ?? r.product_id),
              },
              {
                label: "Quantity",
                render: (r) =>
                  String(r.signed_quantity ?? r.quantity ?? r.size),
              },
              { label: "Entry", render: (r) => format.number(r.entry_price) },
              {
                label: "Protection",
                render: (r) => (
                  <Status
                    value={String(
                      r.protection_verified === true
                        ? "VERIFIED"
                        : (r.protection_status ?? "UNVERIFIED"),
                    )}
                  />
                ),
              },
              {
                label: "Close",
                render: (r) => (
                  <Button
                    variant="danger"
                    disabled={action.isPending}
                    onClick={() =>
                      setConfirmation({
                        title: "Close this demo position?",
                        description:
                          "Submit a reducing demo exchange order. The server verifies managed ownership and current position state.",
                        path: `/demo/positions/${r.product_id}/close`,
                        body: { confirm: true },
                      })
                    }
                  >
                    Review close
                  </Button>
                ),
              },
            ]}
          />
        )}
      </Card>
      <Card className="space-top" title="Demo orders">
        {orders.error ? (
          <ErrorState error={orders.error} />
        ) : (
          <DataTable
            rows={orders.data ?? []}
            getKey={(r) => String(r.id)}
            columns={[
              { label: "Order", render: (r) => String(r.id) },
              { label: "Instrument", render: (r) => String(r.symbol) },
              { label: "Side", render: (r) => String(r.side) },
              { label: "Quantity", render: (r) => String(r.quantity) },
              {
                label: "State",
                render: (r) => <Status value={String(r.state ?? r.status)} />,
              },
              { label: "Created", render: (r) => format.date(r.created_at) },
              {
                label: "Manage",
                render: (r) => (
                  <Button
                    variant="secondary"
                    disabled={
                      action.isPending ||
                      ["FILLED", "CANCELED", "REJECTED"].includes(
                        String(r.state ?? r.status),
                      )
                    }
                    onClick={() =>
                      setConfirmation({
                        title: "Request demo order cancellation?",
                        description:
                          "A cancellation request is not a cancellation acknowledgement. Fills that arrive during cancellation remain part of the exchange position.",
                        path: `/demo/orders/${r.id}/cancel`,
                      })
                    }
                  >
                    Cancel
                  </Button>
                ),
              },
            ]}
          />
        )}
      </Card>
      <Card
        className="space-top"
        title="Observed demo cash ledger"
        subtitle="Signed amounts preserve the exchange denomination. Local baselines are labeled separately from exchange transactions."
      >
        {movements.error ? (
          <ErrorState error={movements.error} />
        ) : (
          <DataTable
            rows={movements.data ?? []}
            getKey={(row) => String(row.id)}
            columns={[
              {
                label: "Movement",
                render: (row) => String(row.transaction_type),
              },
              {
                label: "Amount",
                render: (row) =>
                  row.asset_symbol
                    ? format.money(row.amount, String(row.asset_symbol))
                    : `${String(row.amount)} · denomination unverified`,
              },
              {
                label: "Wallet balance",
                render: (row) =>
                  row.asset_symbol
                    ? format.money(row.balance, String(row.asset_symbol))
                    : "—",
              },
              {
                label: "Exchange timestamp",
                render: (row) => format.date(row.exchange_created_at),
              },
              {
                label: "Observed",
                render: (row) => format.date(row.observed_at),
              },
            ]}
          />
        )}
      </Card>
      <div className="grid-2 space-top">
        <Card title="Demo fills">
          <div className="panel-body">
            {fills.error ? (
              <ErrorState error={fills.error} />
            ) : (
              <JsonView value={fills.data ?? []} />
            )}
          </div>
        </Card>
        <Card title="Execution and reconciliation events">
          <div className="panel-body">
            {events.error ? (
              <ErrorState error={events.error} />
            ) : (
              <JsonView value={events.data ?? []} />
            )}
          </div>
        </Card>
      </div>
      <DemoReportsPanel connected={connected} />
      <Dialog
        open={selectedProduct !== null}
        onOpenChange={(open) => {
          if (!open) setSelectedProduct(null);
        }}
        title={`Verify ${selectedProduct?.symbol ?? "contract"} economics`}
        description="Use current official exchange information or a directly observed demo contract response. Paper assumptions cannot verify demo trading."
      >
        <div className="stack">
          <Field label="Minimum whole contract quantity">
            <Input
              required
              min="1"
              step="1"
              type="number"
              value={minimum}
              onChange={(e) => setMinimum(e.target.value)}
            />
          </Field>
          <Field label="Quantity increment">
            <Input
              required
              min="1"
              step="1"
              type="number"
              value={step}
              onChange={(e) => setStep(e.target.value)}
            />
          </Field>
          <Field label="Initial margin rate (fraction)">
            <Input
              required
              type="number"
              min="0.000001"
              max="1"
              step="any"
              value={margin}
              onChange={(e) => setMargin(e.target.value)}
            />
          </Field>
          <Field label="Maximum quantity covered by reviewed margin tier">
            <Input
              required
              type="number"
              min="1"
              step="1"
              value={maxQuantity}
              onChange={(e) => setMaxQuantity(e.target.value)}
            />
          </Field>
          <Field label="Evidence reference">
            <Textarea
              value={evidence}
              onChange={(e) => setEvidence(e.target.value)}
              placeholder="Official document URL, observed response reference and date"
            />
          </Field>
          {action.error && <Alert tone="red">{action.error.message}</Alert>}
          <Button
            disabled={
              !evidence.trim() ||
              !minimum ||
              !step ||
              !margin ||
              !maxQuantity ||
              action.isPending
            }
            onClick={() =>
              action.mutate({
                path: `/demo/products/${selectedProduct?.id}/verify`,
                body: {
                  min_quantity: Number(minimum),
                  quantity_step: Number(step),
                  initial_margin_rate: margin,
                  max_quantity: Number(maxQuantity),
                  evidence,
                },
              })
            }
          >
            Save verified contract evidence
          </Button>
        </div>
      </Dialog>
      <Dialog
        open={confirmation !== null}
        onOpenChange={(open) => {
          if (!open) setConfirmation(null);
        }}
        title={confirmation?.title ?? "Confirm demo action"}
        description={confirmation?.description}
      >
        {action.error && <Alert tone="red">{action.error.message}</Alert>}
        <div className="form-actions">
          <Button variant="secondary" onClick={() => setConfirmation(null)}>
            Cancel
          </Button>
          <Button
            disabled={action.isPending}
            onClick={() => {
              if (confirmation)
                action.mutate({
                  path: confirmation.path,
                  body: confirmation.body,
                });
            }}
          >
            {action.isPending ? "Submitting…" : "Confirm demo action"}
          </Button>
        </div>
      </Dialog>
    </>
  );
}
