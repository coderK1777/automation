"use client";
import { useState } from "react";
import Link from "next/link";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Bot,
  CheckCircle2,
  Edit3,
  Eye,
  Play,
  Plus,
  Search,
  Square,
  Pause,
  FlaskConical,
  FileDown,
  Copy,
} from "lucide-react";
import { api, format, useApi } from "@/lib/api";
import type { Strategy, Backtest } from "@/lib/types";
import {
  Alert,
  Badge,
  Button,
  Card,
  DataTable,
  Dialog,
  ErrorState,
  Freshness,
  Input,
  JsonView,
  Loading,
  Metric,
  PageTitle,
  Select,
  Status,
} from "./ui";
import StrategyEditor from "./strategy-editor";
export default function StrategiesPage() {
  const query = useApi<Strategy[]>("/strategies", 10000);
  const client = useQueryClient();
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState("ALL");
  const [editor, setEditor] = useState<Strategy | "new" | null>(null);
  const [detail, setDetail] = useState<Strategy | null>(null);
  const [feedback, setFeedback] = useState<string>();
  const [pendingStart, setPendingStart] = useState<Strategy | null>(null);
  const action = useMutation({
    mutationFn: ({ id, kind }: { id: number; kind: string }) =>
      api(`/strategies/${id}/${kind}`, { method: "POST", body: {} }),
    onSuccess: (_, variables) => {
      setFeedback(`${variables.kind.replaceAll("_", " ")} request completed.`);
      setPendingStart(null);
      void client.invalidateQueries();
    },
  });
  const duplicate = useMutation({
    mutationFn: (s: Strategy) =>
      api("/strategies", {
        method: "POST",
        body: {
          name: `${s.name} copy`,
          description: s.description,
          config: s.config,
        },
      }),
    onSuccess: () => {
      setFeedback("Draft strategy duplicated with its own identity.");
      void client.invalidateQueries({ queryKey: ["/strategies"] });
    },
  });
  const data = query.data ?? [],
    rows = data.filter(
      (s) =>
        (filter === "ALL" || s.status === filter) &&
        `${s.name} ${s.config.symbol} ${s.config.kind}`
          .toLowerCase()
          .includes(search.toLowerCase()),
    );
  function exportStrategy(s: Strategy) {
    const url = URL.createObjectURL(
      new Blob([JSON.stringify(s, null, 2)], { type: "application/json" }),
    );
    const a = document.createElement("a");
    a.href = url;
    a.download = `strategy-${s.id}-v${s.version}.json`;
    a.click();
    URL.revokeObjectURL(url);
  }
  return (
    <>
      <PageTitle
        eyebrow="STRATEGY MANAGEMENT"
        title="Your strategy fleet"
        description="Build independent, versioned trading hypotheses. Every deployment passes through validation and portfolio risk controls."
        action={
          <Button onClick={() => setEditor("new")}>
            <Plus size={15} />
            Create strategy
          </Button>
        }
      />
      {feedback && <Alert tone="green">{feedback}</Alert>}
      {(action.error || duplicate.error) && (
        <Alert tone="red">
          {action.error?.message ?? duplicate.error?.message}
        </Alert>
      )}
      <div className="grid-4">
        <Metric
          label="Total strategies"
          value={data.length}
          subtext="Independent configurations"
          icon={<Bot />}
        />
        <Metric
          label="Running"
          value={data.filter((s) => s.status === "RUNNING").length}
          subtext="Autonomous paper workers"
          icon={<Play />}
        />
        <Metric
          label="Paused"
          value={data.filter((s) => s.status === "PAUSED").length}
          subtext="Existing positions remain open"
          icon={<Pause />}
        />
        <Metric
          label="Draft / stopped"
          value={
            data.filter((s) => ["DRAFT", "STOPPED"].includes(s.status)).length
          }
          subtext="No new entries"
          icon={<Square />}
        />
      </div>
      <div className="toolbar space-top">
        <div style={{ position: "relative", flex: 1, maxWidth: 350 }}>
          <Search
            size={15}
            style={{
              position: "absolute",
              left: 11,
              top: 12,
              color: "#7389a8",
            }}
          />
          <Input
            aria-label="Search strategies"
            placeholder="Search strategies or instruments…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            style={{ paddingLeft: 34, maxWidth: "100%" }}
          />
        </div>
        <Select
          aria-label="Filter status"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          style={{ width: 170 }}
        >
          <option value="ALL">All statuses</option>
          {[...new Set(data.map((s) => s.status))].map((s) => (
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
        title="Strategies"
        subtitle="Paper mode · approval is tied to the exact strategy version"
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
          <DataTable<Strategy>
            rows={rows}
            getKey={(s) => s.id}
            empty={
              search
                ? "No strategies match your search"
                : "No strategies yet. Create your first hypothesis."
            }
            columns={[
              {
                label: "Strategy",
                render: (s) => (
                  <div>
                    <button
                      className="table-name"
                      style={{
                        background: "none",
                        border: 0,
                        textAlign: "left",
                        padding: 0,
                      }}
                      onClick={() => setDetail(s)}
                    >
                      {s.name}
                    </button>
                    <div className="table-sub">
                      {s.config.kind.replaceAll("_", " ")} ·{" "}
                      {String(s.id).slice(0, 8)}
                    </div>
                  </div>
                ),
              },
              {
                label: "Instrument",
                render: (s) => (
                  <div>
                    {s.config.symbol}
                    <div className="table-sub">
                      {s.config.timeframe_seconds / 60} min candles
                    </div>
                  </div>
                ),
              },
              {
                label: "Version",
                render: (s) => (
                  <div>
                    v{s.version}
                    <div className="table-sub">
                      {s.approved_version
                        ? `Approved v${s.approved_version}`
                        : "Awaiting approval"}
                    </div>
                  </div>
                ),
              },
              { label: "Status", render: (s) => <Status value={s.status} /> },
              { label: "Mode", render: () => <Badge tone="blue">PAPER</Badge> },
              {
                label: "Actions",
                render: (s) => (
                  <div className="row-actions">
                    <Button
                      variant="ghost"
                      className="btn-small"
                      title="View details"
                      aria-label={`View ${s.name}`}
                      onClick={() => setDetail(s)}
                    >
                      <Eye size={14} />
                    </Button>
                    <Button
                      variant="ghost"
                      className="btn-small"
                      title="Edit version"
                      aria-label={`Edit ${s.name}`}
                      disabled={s.status === "RUNNING"}
                      onClick={() => setEditor(s)}
                    >
                      <Edit3 size={14} />
                    </Button>
                    <Button
                      variant="secondary"
                      className="btn-small"
                      disabled={action.isPending || s.status === "RUNNING"}
                      onClick={() =>
                        action.mutate({ id: s.id, kind: "validate" })
                      }
                    >
                      <CheckCircle2 size={12} />
                      Validate
                    </Button>
                    {s.status === "RUNNING" ? (
                      <Button
                        variant="secondary"
                        className="btn-small"
                        disabled={action.isPending}
                        onClick={() =>
                          action.mutate({ id: s.id, kind: "pause" })
                        }
                      >
                        <Pause size={12} />
                        Pause
                      </Button>
                    ) : (
                      <Button
                        variant="secondary"
                        className="btn-small"
                        disabled={action.isPending}
                        onClick={() => setPendingStart(s)}
                      >
                        <Play size={12} />
                        Paper
                      </Button>
                    )}
                    <Button
                      variant="ghost"
                      className="btn-small"
                      title="Stop strategy; positions remain open"
                      aria-label={`Stop ${s.name}`}
                      disabled={action.isPending}
                      onClick={() => action.mutate({ id: s.id, kind: "stop" })}
                    >
                      <Square size={13} />
                    </Button>
                  </div>
                ),
              },
            ]}
          />
        )}
      </Card>
      <Alert tone="blue">
        <strong>Evidence before deployment.</strong> Validate the saved version,
        import historical candles, run a backtest, then start paper trading.
        Demo and live execution remain restricted by separate backend gates.
      </Alert>
      <Dialog
        open={editor !== null}
        onOpenChange={(open) => !open && setEditor(null)}
        title={editor === "new" ? "Create a strategy" : "Edit strategy version"}
        description="Deterministic built-in models; simulated execution only."
      >
        {editor && (
          <StrategyEditor
            key={typeof editor === "string" ? editor : editor.id}
            strategy={editor === "new" ? undefined : editor}
            onDone={() => setEditor(null)}
          />
        )}
      </Dialog>
      <Dialog
        open={!!pendingStart}
        onOpenChange={(open) => !open && setPendingStart(null)}
        title="Start autonomous paper trading?"
        description="The backend checks validation, backtest evidence, the approved version, contract metadata, account balance and portfolio risk before starting."
      >
        {action.error && <Alert tone="red">{action.error.message}</Alert>}
        <Alert tone="blue">
          {pendingStart?.name} · simulated funds only. The dedicated worker
          continues independently of your browser.
        </Alert>
        <div className="form-actions">
          <Button variant="secondary" onClick={() => setPendingStart(null)}>
            Cancel
          </Button>
          <Button
            disabled={action.isPending}
            onClick={() =>
              pendingStart &&
              action.mutate({ id: pendingStart.id, kind: "start" })
            }
          >
            <Play size={14} />
            {action.isPending ? "Checking readiness…" : "Start paper trading"}
          </Button>
        </div>
      </Dialog>
      <Dialog
        open={!!detail}
        onOpenChange={(open) => !open && setDetail(null)}
        title={detail?.name ?? "Strategy details"}
        description="Current strategy state and immutable version history."
      >
        {detail && (
          <StrategyDetails
            strategy={data.find((s) => s.id === detail.id) ?? detail}
            onEdit={() => {
              setEditor(detail);
              setDetail(null);
            }}
            onDuplicate={() => duplicate.mutate(detail)}
            onExport={() => exportStrategy(detail)}
          />
        )}
      </Dialog>
    </>
  );
}
function StrategyDetails({
  strategy,
  onEdit,
  onDuplicate,
  onExport,
}: {
  strategy: Strategy;
  onEdit: () => void;
  onDuplicate: () => void;
  onExport: () => void;
}) {
  const [tab, setTab] = useState("overview");
  const versions = useApi<Record<string, unknown>[]>(
    `/strategies/${strategy.id}/versions`,
    60000,
  );
  const backtests = useApi<Backtest[]>("/backtests", 15000);
  return (
    <>
      <div className="tabs">
        {["overview", "configuration", "backtests", "versions"].map((t) => (
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
      {tab === "overview" ? (
        <>
          <div className="detail-metadata">
            <div>
              <small>Status</small>
              <Status value={strategy.status} />
            </div>
            <div>
              <small>Current version</small>
              <strong>v{strategy.version}</strong>
            </div>
            <div>
              <small>Approved version</small>
              <strong>
                {strategy.approved_version
                  ? `v${strategy.approved_version}`
                  : "None"}
              </strong>
            </div>
            <div>
              <small>Instrument</small>
              <strong>{strategy.config.symbol}</strong>
            </div>
            <div>
              <small>Timeframe</small>
              <strong>{strategy.config.timeframe_seconds / 60} minutes</strong>
            </div>
            <div>
              <small>Created</small>
              <strong>{format.date(strategy.created_at)}</strong>
            </div>
          </div>
          <p className="list-note">
            {strategy.description || "No research description recorded."}
          </p>
          <div className="button-group">
            <Button
              variant="secondary"
              onClick={onEdit}
              disabled={strategy.status === "RUNNING"}
            >
              <Edit3 size={13} />
              Edit version
            </Button>
            <Button variant="secondary" onClick={onDuplicate}>
              <Copy size={13} />
              Duplicate draft
            </Button>
            <Button variant="secondary" onClick={onExport}>
              <FileDown size={13} />
              Export JSON
            </Button>
            <Link
              className="btn btn-primary"
              href={`/backtesting?strategy=${strategy.id}`}
            >
              <FlaskConical size={13} />
              Backtest
            </Link>
          </div>
        </>
      ) : tab === "configuration" ? (
        <JsonView value={strategy.config} />
      ) : tab === "backtests" ? (
        backtests.isLoading ? (
          <Loading />
        ) : backtests.error ? (
          <ErrorState error={backtests.error} />
        ) : (
          <DataTable<Backtest>
            rows={(backtests.data ?? []).filter(
              (b) => b.strategy_id === strategy.id,
            )}
            getKey={(b) => b.id}
            empty="No historical backtests for this strategy"
            columns={[
              { label: "Run", render: (b) => String(b.id).slice(0, 8) },
              { label: "Status", render: (b) => <Status value={b.status} /> },
              { label: "Created", render: (b) => format.date(b.created_at) },
            ]}
          />
        )
      ) : versions.isLoading ? (
        <Loading />
      ) : versions.error ? (
        <ErrorState error={versions.error} />
      ) : (
        <JsonView value={versions.data} />
      )}
    </>
  );
}
