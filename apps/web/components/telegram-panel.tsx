"use client";
import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
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
  Status,
} from "./ui";
type Row = Record<string, unknown>;
const ids = (value: string) =>
  value
    .split(",")
    .map((v) => v.trim())
    .filter(Boolean);
export default function TelegramPanel() {
  const status = useApi<Row>("/integrations/telegram", 30000);
  const outbox = useApi<Row[]>("/integrations/telegram/outbox", 15000);
  const [enabled, setEnabled] = useState<boolean | null>(null);
  const [chats, setChats] = useState<string | null>(null);
  const [users, setUsers] = useState<string | null>(null);
  const save = useMutation({
    mutationFn: () =>
      api("/integrations/telegram", {
        method: "PUT",
        body: {
          enabled: enabled ?? status.data?.enabled ?? false,
          allowed_chat_ids: ids(
            chats ?? asArray<string>(status.data?.allowed_chat_ids).join(","),
          ),
          allowed_user_ids: ids(
            users ?? asArray<string>(status.data?.allowed_user_ids).join(","),
          ),
        },
      }),
    onSuccess: () => {
      void status.refetch();
      void outbox.refetch();
    },
  });
  const action = useMutation({
    mutationFn: (request: { id: unknown; action: string }) =>
      api(`/integrations/telegram/outbox/${request.id}`, {
        method: "POST",
        body: { action: request.action },
      }),
    onSuccess: () => void outbox.refetch(),
  });
  return (
    <>
      <Card
        className="space-top"
        title="Telegram delivery & read-only commands"
        subtitle="Bot tokens belong in protected server environment settings"
      >
        <div className="panel-body">
          {status.error ? (
            <ErrorState error={status.error} />
          ) : (
            <>
              <div className="health-row">
                <span>Bot token configured</span>
                <Badge>{status.data?.token_configured ? "YES" : "NO"}</Badge>
              </div>
              <div className="health-row">
                <span>Server delivery authorization</span>
                <Badge tone={status.data?.delivery_enabled ? "green" : "amber"}>
                  {status.data?.delivery_enabled ? "ENABLED" : "DISABLED"}
                </Badge>
              </div>
              <Alert tone="blue">
                Allowed commands show account and health information. Remote
                trading controls are disabled. External delivery requires a
                configured token, both allowlists and server delivery
                authorization.
              </Alert>
              <p className="list-note">
                Verification:{" "}
                {String(status.data?.verification ?? "Not recorded")}
              </p>
              <form
                className="stack"
                onSubmit={(e) => {
                  e.preventDefault();
                  save.mutate();
                }}
              >
                <label className="toggle-field">
                  <input
                    type="checkbox"
                    checked={enabled ?? status.data?.enabled === true}
                    onChange={(e) => setEnabled(e.target.checked)}
                  />
                  Enable alert queue and read-only bot commands
                </label>
                <Field
                  label="Allowed Telegram chat IDs"
                  hint="Comma-separated numeric identifiers."
                >
                  <Input
                    value={
                      chats ??
                      asArray<string>(status.data?.allowed_chat_ids).join(", ")
                    }
                    onChange={(e) => setChats(e.target.value)}
                  />
                </Field>
                <Field label="Allowed Telegram user IDs">
                  <Input
                    value={
                      users ??
                      asArray<string>(status.data?.allowed_user_ids).join(", ")
                    }
                    onChange={(e) => setUsers(e.target.value)}
                  />
                </Field>
                {save.error && <Alert tone="red">{save.error.message}</Alert>}
                {save.isSuccess && (
                  <Alert tone="green">Telegram settings saved.</Alert>
                )}
                <Button disabled={save.isPending}>
                  Save Telegram controls
                </Button>
              </form>
              <p className="list-note">
                Commands: {asArray<string>(status.data?.commands).join(" · ")}
              </p>
            </>
          )}
        </div>
      </Card>
      <Card
        className="space-top"
        title="Durable delivery outbox"
        subtitle="Retries can deliver a duplicate after an ambiguous external acknowledgement"
      >
        {action.error && <Alert tone="red">{action.error.message}</Alert>}
        {outbox.error ? (
          <ErrorState error={outbox.error} />
        ) : (
          <DataTable
            rows={outbox.data ?? []}
            getKey={(r) => String(r.id)}
            columns={[
              { label: "Item", render: (r) => String(r.id) },
              { label: "Destination", render: (r) => String(r.chat_id) },
              {
                label: "State",
                render: (r) => <Status value={String(r.status)} />,
              },
              { label: "Attempts", render: (r) => String(r.attempts) },
              {
                label: "Next attempt",
                render: (r) => format.date(r.next_attempt_at),
              },
              { label: "Error", render: (r) => String(r.last_error ?? "—") },
              {
                label: "Manage",
                render: (r) =>
                  r.status === "SENT" ? (
                    "Delivered"
                  ) : (
                    <div className="button-group">
                      <Button
                        variant="secondary"
                        disabled={action.isPending}
                        onClick={() =>
                          action.mutate({ id: r.id, action: "RETRY" })
                        }
                      >
                        Retry
                      </Button>
                      <Button
                        variant="ghost"
                        disabled={action.isPending || r.status === "CANCELED"}
                        onClick={() =>
                          action.mutate({ id: r.id, action: "CANCEL" })
                        }
                      >
                        Cancel
                      </Button>
                    </div>
                  ),
              },
            ]}
          />
        )}
      </Card>
    </>
  );
}
