"use client";
import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { api, asArray, asRecord, format, useApi } from "@/lib/api";
import type { Strategy } from "@/lib/types";
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
  PageTitle,
  Select,
  Textarea,
} from "./ui";
import { Reauthentication } from "./security-panel";
type Row = Record<string, unknown>;
function StrategyReadiness({ strategy }: { strategy: Strategy }) {
  const evidence = useApi<Row>(`/security/readiness/${strategy.id}`, 30000);
  const [category, setCategory] = useState("DEMO_INTEGRATION");
  const [outcome, setOutcome] = useState("PENDING");
  const [uri, setUri] = useState("");
  const [hash, setHash] = useState("");
  const [notes, setNotes] = useState("");
  const save = useMutation({
    mutationFn: () =>
      api(`/security/readiness/${strategy.id}/evidence`, {
        method: "POST",
        body: {
          version: strategy.version,
          category,
          outcome,
          evidence_uri: uri,
          evidence_sha256: hash,
          notes,
        },
      }),
    onSuccess: () => {
      setUri("");
      setHash("");
      setNotes("");
      void evidence.refetch();
    },
  });
  return (
    <>
      <Card
        className="space-top"
        title={`${strategy.name} · version ${strategy.version} readiness`}
        subtitle="Evidence is append-only and applies to this exact strategy version"
      >
        {evidence.isLoading ? (
          <Loading />
        ) : evidence.error ? (
          <ErrorState error={evidence.error} />
        ) : (
          <div className="panel-body">
            {Object.entries(asRecord(evidence.data?.gates)).map(
              ([name, passed]) => (
                <div className="health-row" key={name}>
                  <span>{name.replaceAll("_", " ")}</span>
                  <Badge tone={passed === true ? "green" : "amber"}>
                    {passed === true ? "SATISFIED" : "REQUIRES EVIDENCE"}
                  </Badge>
                </div>
              ),
            )}
            <p className="list-note">
              Completed readiness review does not activate production execution.
            </p>
          </div>
        )}
      </Card>
      <div className="grid-2 space-top">
        <Card
          title="Record acceptance evidence"
          subtitle="Requires enabled MFA and recent identity verification"
        >
          <div className="panel-body">
            {save.error && <Alert tone="red">{save.error.message}</Alert>}
            {save.isSuccess && (
              <Alert tone="green">
                Acceptance evidence recorded for this strategy version.
              </Alert>
            )}
            <form
              className="stack"
              onSubmit={(e) => {
                e.preventDefault();
                save.mutate();
              }}
            >
              <Field label="Acceptance category">
                <Select
                  value={category}
                  onChange={(e) => setCategory(e.target.value)}
                >
                  {[
                    "DEMO_INTEGRATION",
                    "SECURITY_REVIEW",
                    "RISK_REVIEW",
                    "BACKUP_RESTORE",
                    "FAILURE_RECOVERY",
                    "OPERATIONAL_REVIEW",
                  ].map((v) => (
                    <option key={v}>{v}</option>
                  ))}
                </Select>
              </Field>
              <Field label="Observed result">
                <Select
                  value={outcome}
                  onChange={(e) => setOutcome(e.target.value)}
                >
                  <option>PENDING</option>
                  <option>PASS</option>
                  <option>FAIL</option>
                </Select>
              </Field>
              <Field label="Evidence reference">
                <Input
                  required
                  value={uri}
                  onChange={(e) => setUri(e.target.value)}
                />
              </Field>
              <Field label="Evidence SHA-256">
                <Input
                  required
                  pattern="[a-fA-F0-9]{64}"
                  minLength={64}
                  maxLength={64}
                  value={hash}
                  onChange={(e) => setHash(e.target.value)}
                />
              </Field>
              <Field label="Review notes">
                <Textarea
                  value={notes}
                  onChange={(e) => setNotes(e.target.value)}
                />
              </Field>
              <Button disabled={save.isPending}>Record version evidence</Button>
            </form>
          </div>
        </Card>
        <Reauthentication />
      </div>
      <Card className="space-top" title="Acceptance evidence history">
        <DataTable
          rows={asArray<Row>(evidence.data?.evidence)}
          getKey={(r) => String(r.id)}
          columns={[
            { label: "Category", render: (r) => String(r.category) },
            {
              label: "Result",
              render: (r) => (
                <Badge
                  tone={
                    r.outcome === "PASS"
                      ? "green"
                      : r.outcome === "FAIL"
                        ? "red"
                        : "amber"
                  }
                >
                  {String(r.outcome)}
                </Badge>
              ),
            },
            { label: "Reference", render: (r) => String(r.evidence_uri) },
            { label: "Reviewer", render: (r) => String(r.actor) },
            { label: "Recorded", render: (r) => format.date(r.created_at) },
          ]}
        />
      </Card>
    </>
  );
}
export default function LiveReadinessPage() {
  const readiness = useApi<Row>("/live-readiness", 30000);
  const strategies = useApi<Strategy[]>("/strategies", 30000);
  const [id, setId] = useState("");
  const strategy = strategies.data?.find((s) => String(s.id) === id);
  return (
    <>
      <PageTitle
        eyebrow="CONTROLLED PRODUCTION READINESS"
        title="Live readiness"
        description="Review operational evidence for an exact strategy version. Production execution stays disabled while acceptance and explicit authorization are incomplete."
        action={<Badge tone="red">LIVE DISABLED</Badge>}
      />
      <Alert tone="amber">
        {String(
          readiness.data?.message ??
            "Production order routing is disabled. Readiness evidence never authorizes or starts real-money trading.",
        )}
      </Alert>
      <div className="grid-2">
        <Card title="Reported production prerequisites">
          <div className="panel-body">
            {readiness.error ? (
              <ErrorState error={readiness.error} />
            ) : (
              asArray<string>(readiness.data?.missing).map((v) => (
                <div className="readiness-item" key={v}>
                  <Badge tone="amber">PENDING</Badge>
                  <span>{v}</span>
                </div>
              ))
            )}
          </div>
        </Card>
        <Card title="Review a strategy version">
          <div className="panel-body">
            {strategies.error && <ErrorState error={strategies.error} />}
            <Field label="Strategy">
              <Select value={id} onChange={(e) => setId(e.target.value)}>
                <option value="">Choose a saved strategy</option>
                {strategies.data?.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name} · v{s.version}
                  </option>
                ))}
              </Select>
            </Field>
            <div className="form-actions">
              <Button disabled>Production activation unavailable</Button>
            </div>
          </div>
        </Card>
      </div>
      {strategy && (
        <StrategyReadiness
          key={`${strategy.id}:${strategy.version}`}
          strategy={strategy}
        />
      )}
      <Card className="space-top" title="Global readiness evidence">
        <div className="panel-body">
          <JsonView value={readiness.data ?? {}} />
        </div>
      </Card>
    </>
  );
}
