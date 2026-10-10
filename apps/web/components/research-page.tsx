"use client";
import { useState } from "react";
import Link from "next/link";
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
  Loading,
  PageTitle,
  Select,
  Status,
  Textarea,
} from "./ui";
type Row = Record<string, unknown>;
const template = {
  symbol: "BTCUSD",
  timeframe_seconds: 300,
  kind: "EMA_CROSS",
  fast_period: 9,
  slow_period: 21,
  lookback: 20,
  risk_fraction: "0.005",
  stop_loss_pct: "0.01",
  take_profit_pct: "0.02",
  max_leverage: "2",
  allow_short: false,
};
export default function ResearchPage() {
  const client = useQueryClient();
  const documents = useApi<Row[]>("/research/documents", 30000);
  const [title, setTitle] = useState("");
  const [reference, setReference] = useState("");
  const [content, setContent] = useState("");
  const [formatName, setFormatName] = useState("TEXT");
  const [selected, setSelected] = useState<Row | null>(null);
  const [candidate, setCandidate] = useState<Row | null>(null);
  const [spec, setSpec] = useState("");
  const [config, setConfig] = useState(JSON.stringify(template, null, 2));
  const [error, setError] = useState("");
  const [approveOpen, setApproveOpen] = useState(false);
  const [approvedStrategy, setApprovedStrategy] = useState<Row | null>(null);
  const importDocument = useMutation({
    mutationFn: () =>
      api<Row>("/research/documents", {
        method: "POST",
        body: {
          title,
          source_reference: reference,
          format: formatName,
          content,
        },
      }),
    onSuccess: (data) => {
      setSelected(data);
      setCandidate(null);
      setSpec("");
      setApprovedStrategy(null);
      setApproveOpen(false);
      setContent("");
      void documents.refetch();
    },
  });
  const detail = useMutation({
    mutationFn: (id: unknown) => api<Row>(`/research/documents/${id}`),
    onSuccess: (data) => {
      setSelected(data);
      setCandidate(null);
      setSpec("");
      setApprovedStrategy(null);
      setApproveOpen(false);
    },
  });
  const review = useMutation({
    mutationFn: (specification: unknown) =>
      api<Row>(`/research/candidates/${candidate?.id}`, {
        method: "PATCH",
        body: { specification },
      }),
    onSuccess: (data) => {
      setCandidate(data);
      setSpec(JSON.stringify(data.specification, null, 2));
      setSelected((current) =>
        current
          ? {
              ...current,
              candidates: asArray<Row>(current.candidates).map((v) =>
                v.id === data.id ? data : v,
              ),
            }
          : current,
      );
    },
  });
  const approve = useMutation({
    mutationFn: (body: unknown) =>
      api<Row>(`/research/candidates/${candidate?.id}/approve`, {
        method: "POST",
        body: { confirm: true, config: body },
      }),
    onSuccess: (data) => {
      setApproveOpen(false);
      setCandidate(asRecord(data.candidate));
      setApprovedStrategy(asRecord(data.strategy));
      void client.invalidateQueries({ queryKey: ["/strategies"] });
    },
  });
  const loadFile = async (file?: File) => {
    if (!file) return;
    setError("");
    if (file.size > 480000) {
      setError(
        "Import a PDF below 480 KB, or provide extracted text below 250,000 characters.",
      );
      return;
    }
    if (!title) setTitle(file.name.replace(/\.[^.]+$/, ""));
    if (
      file.type === "application/pdf" ||
      file.name.toLowerCase().endsWith(".pdf")
    ) {
      const bytes = new Uint8Array(await file.arrayBuffer());
      let raw = "";
      for (let i = 0; i < bytes.length; i++)
        raw += String.fromCharCode(bytes[i]);
      setContent(btoa(raw));
      setFormatName("PDF_BASE64");
    } else {
      const text = await file.text();
      if (text.length > 250000) {
        setError("Text exceeds 250,000 characters.");
        return;
      }
      setContent(text);
      setFormatName(file.name.endsWith(".md") ? "MARKDOWN" : "TEXT");
    }
  };
  const parseAction = (text: string, next: (value: unknown) => void) => {
    try {
      const value: unknown = JSON.parse(text);
      if (!value || typeof value !== "object" || Array.isArray(value))
        throw new Error("Use a JSON object.");
      setError("");
      next(value);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Invalid JSON");
    }
  };
  const ambiguities = asArray<unknown>(
    asRecord(candidate?.specification).automation_ambiguities,
  );
  return (
    <>
      <PageTitle
        eyebrow="ATTRIBUTED STRATEGY RESEARCH"
        title="Research workspace"
        description="Import authorized source material, review extracted quotations and define explicit mathematical rules before saving a draft strategy."
        action={
          <Link href="/strategies" className="btn btn-secondary">
            Strategy library
          </Link>
        }
      />
      <Alert tone="blue">
        Extracted quotations require your review. Resolve subjective wording,
        thresholds, candle timing and missing rules. Document approval creates a
        draft; validation and backtesting remain separate.
      </Alert>
      {error && <Alert tone="red">{error}</Alert>}
      <div className="grid-2">
        <Card
          title="Import source material"
          subtitle="Text, Markdown or a bounded text-based PDF"
        >
          <div className="panel-body">
            {importDocument.error && (
              <Alert tone="red">{importDocument.error.message}</Alert>
            )}
            <form
              className="stack"
              onSubmit={(e) => {
                e.preventDefault();
                importDocument.mutate();
              }}
            >
              <Field label="Source title">
                <Input
                  required
                  maxLength={200}
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                />
              </Field>
              <Field
                label="Source reference"
                hint="Book edition and chapter, publication URL or an authorized document reference."
              >
                <Input
                  required
                  maxLength={1000}
                  value={reference}
                  onChange={(e) => setReference(e.target.value)}
                />
              </Field>
              <Field label="Upload document">
                <Input
                  type="file"
                  accept=".txt,.md,.pdf,text/plain,text/markdown,application/pdf"
                  onChange={(e) => void loadFile(e.target.files?.[0])}
                />
              </Field>
              <Field label="Document format">
                <Select
                  value={formatName}
                  onChange={(e) => {
                    setFormatName(e.target.value);
                    setContent("");
                  }}
                >
                  <option value="TEXT">Plain text</option>
                  <option value="MARKDOWN">Markdown</option>
                  <option value="PDF_BASE64">PDF file</option>
                </Select>
              </Field>
              {formatName !== "PDF_BASE64" ? (
                <Field label="Source text">
                  <Textarea
                    required
                    maxLength={250000}
                    rows={8}
                    value={content}
                    onChange={(e) => setContent(e.target.value)}
                  />
                </Field>
              ) : (
                <p className="list-note">
                  {content
                    ? "PDF loaded and ready to parse."
                    : "Select a text-based PDF above. Image-only PDFs require extracted text."}
                </p>
              )}
              <Button disabled={importDocument.isPending || !content}>
                {importDocument.isPending
                  ? "Extracting source…"
                  : "Import & extract candidates"}
              </Button>
            </form>
          </div>
        </Card>
        <Card
          title="Source library"
          subtitle="Content hashes identify source revisions"
        >
          {documents.isLoading ? (
            <Loading />
          ) : documents.error ? (
            <ErrorState error={documents.error} />
          ) : (
            <DataTable
              rows={documents.data ?? []}
              getKey={(r) => String(r.id)}
              columns={[
                { label: "Document", render: (r) => String(r.title) },
                { label: "Imported", render: (r) => format.date(r.created_at) },
                { label: "Pages", render: (r) => String(r.page_count) },
                {
                  label: "Review",
                  render: (r) => (
                    <Button
                      variant="secondary"
                      disabled={detail.isPending}
                      onClick={() => detail.mutate(r.id)}
                    >
                      Open
                    </Button>
                  ),
                },
              ]}
            />
          )}
          {detail.error && <Alert tone="red">{detail.error.message}</Alert>}
        </Card>
      </div>
      {selected && (
        <Card
          className="space-top"
          title={String(
            asRecord(selected.document).title ?? "Extracted candidates",
          )}
          subtitle={`Source: ${asRecord(selected.document).source_reference ?? ""}`}
        >
          <DataTable
            rows={asArray<Row>(selected.candidates)}
            getKey={(r) => String(r.id)}
            columns={[
              { label: "Candidate", render: (r) => String(r.name) },
              {
                label: "Source location",
                render: (r) => String(r.source_location),
              },
              {
                label: "Review state",
                render: (r) => <Status value={String(r.status)} />,
              },
              {
                label: "Open",
                render: (r) => (
                  <Button
                    variant="secondary"
                    onClick={() => {
                      setCandidate(r);
                      setSpec(JSON.stringify(r.specification, null, 2));
                      setApprovedStrategy(null);
                    }}
                  >
                    Review rules
                  </Button>
                ),
              },
            ]}
          />
        </Card>
      )}
      {candidate && (
        <div className="space-top">
          <div className="grid-2">
            <Card title={String(candidate.name)} subtitle="Source quotation">
              <div className="panel-body">
                <pre className="research-excerpt">
                  {String(candidate.excerpt ?? "")}
                </pre>
                <Badge>
                  {String(
                    asRecord(candidate.specification)
                      .confidence_in_extraction ?? "OPERATOR REVIEW REQUIRED",
                  )}
                </Badge>
                {ambiguities.length > 0 && (
                  <Alert tone="amber">
                    <strong>Unresolved automation questions</strong>
                    <ul>
                      {ambiguities.map((v, i) => (
                        <li key={i}>{String(v)}</li>
                      ))}
                    </ul>
                  </Alert>
                )}
              </div>
            </Card>
            <Card
              title="Reviewed strategy specification"
              subtitle="Preserve attribution. Resolve every ambiguity explicitly before approving."
            >
              <div className="panel-body">
                {review.error && (
                  <Alert tone="red">{review.error.message}</Alert>
                )}
                {review.isSuccess && <Alert tone="green">Review saved.</Alert>}
                <Field label="Mathematical rules & extraction review">
                  <Textarea
                    className="code-input"
                    rows={22}
                    readOnly={candidate.status === "APPROVED"}
                    value={spec}
                    onChange={(e) => setSpec(e.target.value)}
                  />
                </Field>
                <div className="form-actions">
                  <Button
                    disabled={
                      candidate.status === "APPROVED" || review.isPending
                    }
                    onClick={() => parseAction(spec, (v) => review.mutate(v))}
                  >
                    Save reviewed specification
                  </Button>
                </div>
              </div>
            </Card>
          </div>
          <Card
            className="space-top"
            title="Executable draft configuration"
            subtitle="Define this candidate using the strategy schema; the starting template is an operator choice, not an extracted trading rule."
          >
            <div className="panel-body">
              <Field label="Closed-candle strategy configuration">
                <Textarea
                  className="code-input"
                  rows={14}
                  value={config}
                  onChange={(e) => setConfig(e.target.value)}
                />
              </Field>
              {approve.error && (
                <Alert tone="red">{approve.error.message}</Alert>
              )}
              {approvedStrategy && (
                <Alert tone="green">
                  Draft strategy {String(approvedStrategy.name)} created.{" "}
                  <Link href="/strategies" className="subtle-link">
                    Validate and backtest it →
                  </Link>
                </Alert>
              )}
              <div className="form-actions">
                <Button
                  disabled={
                    candidate.status !== "REVIEWED" ||
                    ambiguities.length > 0 ||
                    approve.isPending
                  }
                  onClick={() => setApproveOpen(true)}
                >
                  Review & save draft strategy
                </Button>
              </div>
              <p className="list-note">
                To approve, save the reviewed specification with an empty
                automation_ambiguities list only after each question is resolved
                in explicit rules. Approval cannot start execution.
              </p>
            </div>
          </Card>
        </div>
      )}
      <Dialog
        open={approveOpen}
        onOpenChange={setApproveOpen}
        title="Approve this research definition?"
        description="Confirm that you resolved the quoted ambiguities and reviewed this exact executable configuration. A new draft strategy will be created."
      >
        <JsonView value={asRecord(candidate?.specification).test_cases} />
        {approve.error && <Alert tone="red">{approve.error.message}</Alert>}
        <div className="form-actions">
          <Button variant="secondary" onClick={() => setApproveOpen(false)}>
            Cancel
          </Button>
          <Button
            disabled={approve.isPending}
            onClick={() => parseAction(config, (v) => approve.mutate(v))}
          >
            Confirm & create draft
          </Button>
        </div>
      </Dialog>
    </>
  );
}
