"use client";
import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api, asArray, format, useApi } from "@/lib/api";
import {
  Alert,
  Badge,
  Button,
  Card,
  DataTable,
  ErrorState,
  Field,
  Input,
  Loading,
  PageTitle,
  Select,
  Status,
  Textarea,
} from "./ui";
type Row = Record<string, unknown>;
const categories = [
  "ECONOMIC",
  "CRYPTO",
  "MAINTENANCE",
  "SUSPENSION",
  "FUNDING",
  "VOLATILITY",
];
const symbols = (value: string) =>
  value
    .split(",")
    .map((v) => v.trim().toUpperCase())
    .filter(Boolean);
export default function NewsPage() {
  const client = useQueryClient();
  const sources = useApi<Row[]>("/news/sources", 30000);
  const events = useApi<Row[]>("/news/events", 30000);
  const blackouts = useApi<Row[]>("/news/blackouts", 30000);
  const [checkSymbol, setCheckSymbol] = useState("BTCUSD");
  const check = useApi<Row>(
    `/news/entry-check?symbol=${encodeURIComponent(checkSymbol)}`,
    15000,
  );
  const [sourceId, setSourceId] = useState<number | null>(null);
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [requiredSource, setRequiredSource] = useState(false);
  const [sourceEnabled, setSourceEnabled] = useState(true);
  const [stale, setStale] = useState("3600");
  const [authorized, setAuthorized] = useState(false);
  const [eventSource, setEventSource] = useState("");
  const [externalId, setExternalId] = useState("");
  const [title, setTitle] = useState("");
  const [category, setCategory] = useState("ECONOMIC");
  const [impact, setImpact] = useState("HIGH");
  const [published, setPublished] = useState("");
  const [scheduled, setScheduled] = useState("");
  const [eventSymbols, setEventSymbols] = useState("");
  const [sourceUrl, setSourceUrl] = useState("");
  const [details, setDetails] = useState("");
  const [ruleId, setRuleId] = useState<number | null>(null);
  const [ruleName, setRuleName] = useState("");
  const [ruleCategories, setRuleCategories] = useState(["ECONOMIC"]);
  const [minimumImpact, setMinimumImpact] = useState("HIGH");
  const [before, setBefore] = useState("30");
  const [after, setAfter] = useState("30");
  const [ruleSymbols, setRuleSymbols] = useState("");
  const [ruleEnabled, setRuleEnabled] = useState(true);
  const saveSource = useMutation({
    mutationFn: () =>
      api("/news/sources" + (sourceId ? `/${sourceId}` : ""), {
        method: sourceId ? "PUT" : "POST",
        body: {
          name,
          url: url || null,
          authorized,
          enabled: sourceEnabled,
          required_for_trading: requiredSource,
          stale_after_seconds: Number(stale),
        },
      }),
    onSuccess: () => {
      setName("");
      setUrl("");
      setSourceId(null);
      setAuthorized(false);
      void client.invalidateQueries();
    },
  });
  const saveEvent = useMutation({
    mutationFn: () =>
      api("/news/events", {
        method: "POST",
        body: {
          source_id: Number(eventSource),
          external_id: externalId,
          title,
          category,
          impact,
          published_at: published,
          scheduled_at: scheduled || null,
          symbols: symbols(eventSymbols),
          source_url: sourceUrl || null,
          details,
        },
      }),
    onSuccess: () => {
      setExternalId("");
      setTitle("");
      setDetails("");
      void client.invalidateQueries();
    },
  });
  const saveRule = useMutation({
    mutationFn: () =>
      api("/news/blackouts" + (ruleId ? `/${ruleId}` : ""), {
        method: ruleId ? "PUT" : "POST",
        body: {
          name: ruleName,
          categories: ruleCategories,
          minimum_impact: minimumImpact,
          before_minutes: Number(before),
          after_minutes: Number(after),
          symbols: symbols(ruleSymbols),
          enabled: ruleEnabled,
        },
      }),
    onSuccess: () => {
      setRuleId(null);
      setRuleName("");
      void client.invalidateQueries();
    },
  });
  return (
    <>
      <PageTitle
        eyebrow="ATTRIBUTED EVENTS & ENTRY FILTERS"
        title="News & event controls"
        description="Authorize sources, preserve publication and receipt timing, and block new entries around configured events. Events cannot create trades."
      />
      <Card
        title="Current event entry check"
        action={
          <Select
            aria-label="Event check instrument"
            value={checkSymbol}
            onChange={(e) => setCheckSymbol(e.target.value)}
          >
            <option>BTCUSD</option>
            <option>ETHUSD</option>
          </Select>
        }
      >
        <div className="panel-body">
          {check.isLoading ? (
            <Loading />
          ) : check.error ? (
            <ErrorState error={check.error} />
          ) : (
            <>
              <Badge tone={check.data?.allowed ? "green" : "amber"}>
                {check.data?.allowed
                  ? "EVENT FILTER ALLOWS ENTRY"
                  : "EVENT FILTER BLOCKS ENTRY"}
              </Badge>
              <p className="list-note">
                As of {format.date(check.data?.at)} IST. Independent portfolio
                checks still apply.
              </p>
              {asArray<Row>(check.data?.reasons).map((r, i) => (
                <Alert tone="amber" key={i}>
                  {String(r.title ?? r.source ?? r.kind)}
                </Alert>
              ))}
            </>
          )}
        </div>
      </Card>
      <div className="grid-2 space-top">
        <Card
          title={sourceId ? "Edit authorized source" : "Authorize a source"}
          subtitle="Use a licensed or otherwise authorized source; URLs require the server host allowlist"
        >
          <div className="panel-body">
            {saveSource.error && (
              <Alert tone="red">{saveSource.error.message}</Alert>
            )}
            {saveSource.isSuccess && (
              <Alert tone="green">Source configuration saved.</Alert>
            )}
            <form
              className="stack"
              onSubmit={(e) => {
                e.preventDefault();
                saveSource.mutate();
              }}
            >
              <Field label="Source name">
                <Input
                  required
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                />
              </Field>
              <Field label="Optional JSON event feed URL">
                <Input
                  type="url"
                  placeholder="https://authorized-provider.example/events"
                  value={url}
                  onChange={(e) => setUrl(e.target.value)}
                />
              </Field>
              <Field label="Freshness limit (seconds)">
                <Input
                  type="number"
                  min="60"
                  max="604800"
                  required
                  value={stale}
                  onChange={(e) => setStale(e.target.value)}
                />
              </Field>
              <label className="toggle-field">
                <input
                  type="checkbox"
                  checked={sourceEnabled}
                  onChange={(e) => setSourceEnabled(e.target.checked)}
                />
                Enable this source
              </label>
              <label className="toggle-field">
                <input
                  type="checkbox"
                  checked={requiredSource}
                  onChange={(e) => setRequiredSource(e.target.checked)}
                />
                Block entries if this required source becomes stale
              </label>
              <label className="toggle-field">
                <input
                  type="checkbox"
                  required
                  checked={authorized}
                  onChange={(e) => setAuthorized(e.target.checked)}
                />
                I am authorized to use this source
              </label>
              <div className="button-group">
                <Button disabled={saveSource.isPending || !authorized}>
                  {sourceId ? "Save source" : "Authorize source"}
                </Button>
                {sourceId && (
                  <Button
                    type="button"
                    variant="secondary"
                    onClick={() => {
                      setSourceId(null);
                      setName("");
                      setUrl("");
                      setAuthorized(false);
                    }}
                  >
                    Cancel edit
                  </Button>
                )}
              </div>
            </form>
          </div>
        </Card>
        <Card
          title="Source status"
          subtitle="A configured source is not evidence of a verified feed"
        >
          {sources.error ? (
            <ErrorState error={sources.error} />
          ) : (
            <DataTable
              rows={sources.data ?? []}
              getKey={(r) => String(r.id)}
              columns={[
                { label: "Source", render: (r) => String(r.name) },
                { label: "Enabled", render: (r) => (r.enabled ? "Yes" : "No") },
                {
                  label: "Last success",
                  render: (r) => format.date(r.last_success_at),
                },
                {
                  label: "State",
                  render: (r) =>
                    String(
                      r.last_error ??
                        (r.last_success_at ? "Observed" : "Awaiting data"),
                    ),
                },
                {
                  label: "Edit",
                  render: (r) => (
                    <Button
                      variant="secondary"
                      onClick={() => {
                        setSourceId(Number(r.id));
                        setName(String(r.name));
                        setUrl(String(r.url ?? ""));
                        setRequiredSource(r.required_for_trading === true);
                        setSourceEnabled(r.enabled === true);
                        setStale(String(r.stale_after_seconds));
                        setAuthorized(false);
                      }}
                    >
                      Edit
                    </Button>
                  ),
                },
              ]}
            />
          )}
        </Card>
      </div>
      <Card
        className="space-top"
        title="Import an attributed event"
        subtitle="Use ISO 8601 timestamps with a timezone, for example 2026-10-09T15:00:00+05:30"
      >
        <div className="panel-body">
          {saveEvent.error && (
            <Alert tone="red">{saveEvent.error.message}</Alert>
          )}
          {saveEvent.isSuccess && (
            <Alert tone="green">
              Event recorded; duplicates preserve the original attribution.
            </Alert>
          )}
          <form
            className="form-grid"
            onSubmit={(e) => {
              e.preventDefault();
              saveEvent.mutate();
            }}
          >
            <Field label="Authorized source">
              <Select
                required
                value={eventSource}
                onChange={(e) => setEventSource(e.target.value)}
              >
                <option value="">Select a source</option>
                {sources.data
                  ?.filter((r) => r.enabled)
                  .map((r) => (
                    <option key={String(r.id)} value={String(r.id)}>
                      {String(r.name)}
                    </option>
                  ))}
              </Select>
            </Field>
            <Field label="Provider event ID">
              <Input
                required
                value={externalId}
                onChange={(e) => setExternalId(e.target.value)}
              />
            </Field>
            <div className="form-full">
              <Field label="Event title">
                <Input
                  required
                  maxLength={300}
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                />
              </Field>
            </div>
            <Field label="Category">
              <Select
                value={category}
                onChange={(e) => setCategory(e.target.value)}
              >
                {categories.map((c) => (
                  <option key={c}>{c}</option>
                ))}
              </Select>
            </Field>
            <Field label="Impact">
              <Select
                value={impact}
                onChange={(e) => setImpact(e.target.value)}
              >
                {["LOW", "MEDIUM", "HIGH"].map((v) => (
                  <option key={v}>{v}</option>
                ))}
              </Select>
            </Field>
            <Field label="Published timestamp with timezone">
              <Input
                required
                placeholder="2026-10-09T10:00:00+05:30"
                value={published}
                onChange={(e) => setPublished(e.target.value)}
              />
            </Field>
            <Field label="Scheduled event timestamp (optional)">
              <Input
                placeholder="2026-10-09T15:00:00+05:30"
                value={scheduled}
                onChange={(e) => setScheduled(e.target.value)}
              />
            </Field>
            <Field
              label="Symbols (comma separated)"
              hint="Leave blank to apply to all instruments."
            >
              <Input
                placeholder="BTCUSD, ETHUSD"
                value={eventSymbols}
                onChange={(e) => setEventSymbols(e.target.value)}
              />
            </Field>
            <Field label="Attribution URL">
              <Input
                type="url"
                value={sourceUrl}
                onChange={(e) => setSourceUrl(e.target.value)}
              />
            </Field>
            <div className="form-full">
              <Field label="Attributed details">
                <Textarea
                  value={details}
                  onChange={(e) => setDetails(e.target.value)}
                />
              </Field>
            </div>
            <div className="form-actions form-full">
              <Button disabled={saveEvent.isPending}>
                Record attributed event
              </Button>
            </div>
          </form>
        </div>
      </Card>
      <div className="grid-2 space-top">
        <Card
          title={ruleId ? "Edit blackout rule" : "Define an event blackout"}
          subtitle="Stops new entries; existing positions retain their protective management"
        >
          <div className="panel-body">
            {saveRule.error && (
              <Alert tone="red">{saveRule.error.message}</Alert>
            )}
            {saveRule.isSuccess && (
              <Alert tone="green">Blackout rule saved.</Alert>
            )}
            <form
              className="stack"
              onSubmit={(e) => {
                e.preventDefault();
                saveRule.mutate();
              }}
            >
              <Field label="Rule name">
                <Input
                  required
                  value={ruleName}
                  onChange={(e) => setRuleName(e.target.value)}
                />
              </Field>
              <div className="category-options">
                {categories.map((c) => (
                  <label className="toggle-field" key={c}>
                    <input
                      type="checkbox"
                      checked={ruleCategories.includes(c)}
                      onChange={(e) =>
                        setRuleCategories(
                          e.target.checked
                            ? [...ruleCategories, c]
                            : ruleCategories.filter((v) => v !== c),
                        )
                      }
                    />
                    {c.replaceAll("_", " ")}
                  </label>
                ))}
              </div>
              <Field label="Minimum event impact">
                <Select
                  value={minimumImpact}
                  onChange={(e) => setMinimumImpact(e.target.value)}
                >
                  {["LOW", "MEDIUM", "HIGH"].map((v) => (
                    <option key={v}>{v}</option>
                  ))}
                </Select>
              </Field>
              <div className="form-grid">
                <Field label="Minutes before">
                  <Input
                    required
                    type="number"
                    min="0"
                    max="10080"
                    value={before}
                    onChange={(e) => setBefore(e.target.value)}
                  />
                </Field>
                <Field label="Minutes after">
                  <Input
                    required
                    type="number"
                    min="0"
                    max="10080"
                    value={after}
                    onChange={(e) => setAfter(e.target.value)}
                  />
                </Field>
              </div>
              <Field label="Symbols (comma separated)">
                <Input
                  value={ruleSymbols}
                  onChange={(e) => setRuleSymbols(e.target.value)}
                  placeholder="Blank means all instruments"
                />
              </Field>
              <label className="toggle-field">
                <input
                  type="checkbox"
                  checked={ruleEnabled}
                  onChange={(e) => setRuleEnabled(e.target.checked)}
                />
                Enable this rule
              </label>
              <Button disabled={saveRule.isPending || !ruleCategories.length}>
                {ruleId ? "Save blackout" : "Create blackout"}
              </Button>
            </form>
          </div>
        </Card>
        <Card title="Blackout rules">
          {blackouts.error ? (
            <ErrorState error={blackouts.error} />
          ) : (
            <DataTable
              rows={blackouts.data ?? []}
              getKey={(r) => String(r.id)}
              columns={[
                { label: "Rule", render: (r) => String(r.name) },
                {
                  label: "Window",
                  render: (r) =>
                    `${r.before_minutes}m before / ${r.after_minutes}m after`,
                },
                { label: "Impact", render: (r) => String(r.minimum_impact) },
                {
                  label: "State",
                  render: (r) => (
                    <Status value={r.enabled ? "ENABLED" : "DISABLED"} />
                  ),
                },
                {
                  label: "Edit",
                  render: (r) => (
                    <Button
                      variant="secondary"
                      onClick={() => {
                        setRuleId(Number(r.id));
                        setRuleName(String(r.name));
                        setRuleCategories(asArray<string>(r.categories));
                        setMinimumImpact(String(r.minimum_impact));
                        setBefore(String(r.before_minutes));
                        setAfter(String(r.after_minutes));
                        setRuleSymbols(asArray<string>(r.symbols).join(", "));
                        setRuleEnabled(r.enabled === true);
                      }}
                    >
                      Edit
                    </Button>
                  ),
                },
              ]}
            />
          )}
        </Card>
      </div>
      <Card
        className="space-top"
        title="Attributed event history"
        subtitle="Publication and receipt timestamps remain separate for causal evaluation"
      >
        {events.error ? (
          <ErrorState error={events.error} />
        ) : (
          <DataTable
            rows={events.data ?? []}
            getKey={(r) => String(r.id)}
            columns={[
              { label: "Event", render: (r) => String(r.title) },
              { label: "Category", render: (r) => String(r.category) },
              {
                label: "Impact",
                render: (r) => (
                  <Badge tone={r.impact === "HIGH" ? "amber" : "neutral"}>
                    {String(r.impact)}
                  </Badge>
                ),
              },
              {
                label: "Published",
                render: (r) => format.date(r.published_at),
              },
              {
                label: "Scheduled",
                render: (r) => format.date(r.scheduled_at),
              },
              { label: "Received", render: (r) => format.date(r.received_at) },
            ]}
          />
        )}
      </Card>
    </>
  );
}
