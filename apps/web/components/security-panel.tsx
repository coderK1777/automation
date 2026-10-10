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
  Loading,
  Select,
} from "./ui";
type Row = Record<string, unknown>;
export function Reauthentication({ onVerified }: { onVerified?: () => void }) {
  const status = useApi<Row>("/security/status", 30000);
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [recovery, setRecovery] = useState("");
  const client = useQueryClient();
  const reauth = useMutation({
    mutationFn: () =>
      api<Row>("/security/reauth", {
        method: "POST",
        body: {
          password,
          code: code || undefined,
          recovery_code: recovery || undefined,
        },
      }),
    onSuccess: () => {
      setPassword("");
      setCode("");
      setRecovery("");
      void client.invalidateQueries({ queryKey: ["/security/status"] });
      onVerified?.();
    },
  });
  const verified = asRecord(status.data?.reauth);
  return (
    <Card
      title="Verify sensitive actions"
      subtitle="Verification expires. MFA is required when it is enrolled."
    >
      <div className="panel-body">
        {status.error && <ErrorState error={status.error} />}
        {verified.password_verified === true ? (
          <Alert tone="green">
            Session verification recorded until{" "}
            {format.date(verified.expires_at)} IST.
          </Alert>
        ) : (
          <p className="list-note">
            Confirm your identity before enabling demo execution, changing
            credentials or managing users.
          </p>
        )}
        {reauth.error && <Alert tone="red">{reauth.error.message}</Alert>}
        <form
          onSubmit={(e) => {
            e.preventDefault();
            reauth.mutate();
          }}
          className="stack"
        >
          <Field label="Current password">
            <Input
              type="password"
              autoComplete="current-password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </Field>
          {status.data?.mfa_enabled === true && (
            <>
              <Field label="Authenticator code">
                <Input
                  inputMode="numeric"
                  autoComplete="one-time-code"
                  value={code}
                  onChange={(e) => setCode(e.target.value)}
                />
              </Field>
              <Field
                label="Recovery code"
                hint="Use only if your authenticator is unavailable."
              >
                <Input
                  autoComplete="off"
                  value={recovery}
                  onChange={(e) => setRecovery(e.target.value)}
                />
              </Field>
            </>
          )}
          <Button disabled={reauth.isPending}>
            {reauth.isPending ? "Verifying…" : "Verify identity"}
          </Button>
        </form>
      </div>
    </Card>
  );
}
export default function SecurityPanel() {
  const client = useQueryClient();
  const status = useApi<Row>("/security/status", 30000);
  const sessions = useApi<Row[]>("/security/sessions", 30000);
  const users = useApi<Row[]>("/security/users", 30000);
  const [enrollment, setEnrollment] = useState<Row | null>(null);
  const [code, setCode] = useState("");
  const [recoveryCodes, setRecoveryCodes] = useState<string[]>([]);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState("viewer");
  const [newPassword, setNewPassword] = useState("");
  const [currentPassword, setCurrentPassword] = useState("");
  const [passwordCode, setPasswordCode] = useState("");
  const [passwordRecovery, setPasswordRecovery] = useState("");
  const [confirmation, setConfirmation] = useState<{
    title: string;
    path: string;
    method: string;
    body?: unknown;
  } | null>(null);
  const action = useMutation({
    mutationFn: (request: { path: string; method?: string; body?: unknown }) =>
      api<Row>(request.path, {
        method: request.method ?? "POST",
        body: request.body,
      }),
    onSuccess: () => {
      setConfirmation(null);
      void client.invalidateQueries();
    },
  });
  const enroll = useMutation({
    mutationFn: () =>
      api<Row>("/security/mfa/enroll", { method: "POST", body: {} }),
    onSuccess: setEnrollment,
  });
  const confirm = useMutation({
    mutationFn: () =>
      api<Row>("/security/mfa/confirm", { method: "POST", body: { code } }),
    onSuccess: (data) => {
      setEnrollment(null);
      setCode("");
      setRecoveryCodes(asArray<string>(data.recovery_codes));
      void client.invalidateQueries();
    },
  });
  const recover = useMutation({
    mutationFn: () =>
      api<Row>("/security/mfa/recovery-codes", { method: "POST", body: {} }),
    onSuccess: (data) => setRecoveryCodes(asArray<string>(data.recovery_codes)),
  });
  const create = useMutation({
    mutationFn: () =>
      api("/security/users", {
        method: "POST",
        body: { username, password, role },
      }),
    onSuccess: () => {
      setUsername("");
      setPassword("");
      void users.refetch();
    },
  });
  const changePassword = useMutation({
    mutationFn: () =>
      api("/security/password", {
        method: "POST",
        body: {
          current_password: currentPassword,
          new_password: newPassword,
          code: passwordCode || undefined,
          recovery_code: passwordRecovery || undefined,
        },
      }),
    onSuccess: () => {
      setCurrentPassword("");
      setNewPassword("");
      setPasswordCode("");
      setPasswordRecovery("");
      void sessions.refetch();
    },
  });
  return (
    <div className="space-top">
      <div className="grid-2">
        <Reauthentication />
        <Card
          title="Multi-factor authentication"
          subtitle="Time-based authenticator and single-use recovery codes"
        >
          <div className="panel-body">
            {status.isLoading ? (
              <Loading />
            ) : status.error ? (
              <ErrorState error={status.error} />
            ) : (
              <>
                <div className="health-row">
                  <span>Authenticator</span>
                  <Badge tone={status.data?.mfa_enabled ? "green" : "amber"}>
                    {status.data?.mfa_enabled ? "ENROLLED" : "NOT ENROLLED"}
                  </Badge>
                </div>
                <div className="button-group">
                  <Button
                    onClick={() => enroll.mutate()}
                    disabled={
                      enroll.isPending || status.data?.mfa_enabled === true
                    }
                  >
                    Enroll authenticator
                  </Button>
                  {status.data?.mfa_enabled === true && (
                    <>
                      <Button
                        variant="secondary"
                        onClick={() => recover.mutate()}
                        disabled={recover.isPending}
                      >
                        Replace recovery codes
                      </Button>
                      <Button
                        variant="danger"
                        onClick={() =>
                          setConfirmation({
                            title: "Disable MFA?",
                            path: "/security/mfa/disable",
                            method: "POST",
                            body: { confirm: "DISABLE_MFA" },
                          })
                        }
                      >
                        Disable MFA
                      </Button>
                    </>
                  )}
                </div>
              </>
            )}
            {(enroll.error || recover.error || action.error) && (
              <Alert tone="red">
                {(enroll.error || recover.error || action.error)?.message}
              </Alert>
            )}
            {asRecord(status.data?.vault).configured !== true && (
              <Alert tone="amber">
                Initialize the protected server credential vault before
                enrolling MFA. Secret keys are generated and retained on the
                server.
              </Alert>
            )}
            <p className="list-note">
              Enrollment secrets and recovery codes appear once. Store recovery
              codes in your password manager.
            </p>
          </div>
        </Card>
      </div>
      <Card
        className="space-top"
        title="Active sessions"
        subtitle="Revoke a session to end its browser and event stream access"
      >
        {sessions.error ? (
          <ErrorState error={sessions.error} />
        ) : (
          <DataTable
            rows={sessions.data ?? []}
            getKey={(r) => String(r.id)}
            columns={[
              {
                label: "Session",
                render: (r) => (
                  <>
                    {String(r.id).slice(0, 14)}{" "}
                    {r.current === true && <Badge tone="blue">CURRENT</Badge>}
                  </>
                ),
              },
              { label: "Expires", render: (r) => format.date(r.expires_at) },
              {
                label: "Access",
                render: (r) => (
                  <Button
                    variant="secondary"
                    disabled={action.isPending}
                    onClick={() =>
                      setConfirmation({
                        title: r.current
                          ? "Sign out this session?"
                          : "Revoke this session?",
                        path: `/security/sessions/${r.id}`,
                        method: "DELETE",
                      })
                    }
                  >
                    Revoke
                  </Button>
                ),
              },
            ]}
          />
        )}
      </Card>
      <div className="grid-2 space-top">
        <Card
          title="Change password"
          subtitle="Other sessions are revoked when your password changes"
        >
          <div className="panel-body">
            {changePassword.error && (
              <Alert tone="red">{changePassword.error.message}</Alert>
            )}
            {changePassword.isSuccess && (
              <Alert tone="green">
                Password updated. Other sessions were revoked.
              </Alert>
            )}
            <form
              className="stack"
              onSubmit={(e) => {
                e.preventDefault();
                changePassword.mutate();
              }}
            >
              <Field label="Current password">
                <Input
                  type="password"
                  autoComplete="current-password"
                  required
                  value={currentPassword}
                  onChange={(e) => setCurrentPassword(e.target.value)}
                />
              </Field>
              <Field label="New password">
                <Input
                  type="password"
                  autoComplete="new-password"
                  minLength={12}
                  required
                  value={newPassword}
                  onChange={(e) => setNewPassword(e.target.value)}
                />
              </Field>
              {status.data?.mfa_enabled === true && (
                <>
                  <Field label="Authenticator code">
                    <Input
                      autoComplete="one-time-code"
                      inputMode="numeric"
                      value={passwordCode}
                      onChange={(e) => setPasswordCode(e.target.value)}
                    />
                  </Field>
                  <Field label="Recovery code for password change">
                    <Input
                      autoComplete="off"
                      value={passwordRecovery}
                      onChange={(e) => setPasswordRecovery(e.target.value)}
                    />
                  </Field>
                </>
              )}
              <Button disabled={changePassword.isPending}>
                Update password
              </Button>
            </form>
          </div>
        </Card>
        <Card
          title="Credential bindings"
          subtitle="Secret values are configured on the server"
        >
          <div className="panel-body">
            <div className="health-row">
              <span>Encrypted credential vault</span>
              <Badge>
                {asRecord(status.data?.vault).configured === true
                  ? "CONFIGURED"
                  : "NOT CONFIGURED"}
              </Badge>
            </div>
            <DataTable
              rows={asArray<Row>(asRecord(status.data?.vault).credentials)}
              getKey={(r) => String(r.name)}
              empty="No encrypted credential bindings"
              columns={[
                { label: "Binding", render: (r) => String(r.name) },
                { label: "Environment", render: (r) => String(r.environment) },
                { label: "Updated", render: (r) => format.date(r.updated_at) },
              ]}
            />
            <p className="list-note">
              Provide demo API credentials through protected server environment
              settings or the local credential management CLI. Exchange keys are
              never accepted by the browser.
            </p>
          </div>
        </Card>
      </div>
      <Card
        className="space-top"
        title="Workspace users"
        subtitle="Operator role and identity verification are required for account administration"
      >
        {users.error ? (
          <ErrorState error={users.error} />
        ) : (
          <DataTable
            rows={users.data ?? []}
            getKey={(r) => String(r.id)}
            columns={[
              { label: "User", render: (r) => String(r.username) },
              { label: "Role", render: (r) => <Badge>{String(r.role)}</Badge> },
              {
                label: "State",
                render: (r) => (
                  <Badge tone={r.active ? "green" : "neutral"}>
                    {r.active ? "ACTIVE" : "DISABLED"}
                  </Badge>
                ),
              },
              {
                label: "MFA",
                render: (r) => (r.mfa_enabled ? "Enrolled" : "Not enrolled"),
              },
              {
                label: "Manage",
                render: (r) => (
                  <div className="button-group">
                    <Button
                      variant="secondary"
                      onClick={() =>
                        setConfirmation({
                          title: `${r.active ? "Disable" : "Enable"} ${r.username}?`,
                          path: `/security/users/${r.id}`,
                          method: "PATCH",
                          body: { active: !r.active },
                        })
                      }
                    >
                      {r.active ? "Disable" : "Enable"}
                    </Button>
                    <Button
                      variant="secondary"
                      onClick={() =>
                        setConfirmation({
                          title: `Change ${r.username} to ${r.role === "operator" ? "viewer" : "operator"}?`,
                          path: `/security/users/${r.id}`,
                          method: "PATCH",
                          body: {
                            role: r.role === "operator" ? "viewer" : "operator",
                          },
                        })
                      }
                    >
                      Change role
                    </Button>
                    <Button
                      variant="ghost"
                      onClick={() =>
                        setConfirmation({
                          title: `Revoke all sessions for ${r.username}?`,
                          path: `/security/users/${r.id}/sessions`,
                          method: "DELETE",
                        })
                      }
                    >
                      Revoke sessions
                    </Button>
                  </div>
                ),
              },
            ]}
          />
        )}
        <div className="panel-body">
          {create.error && <Alert tone="red">{create.error.message}</Alert>}
          {create.isSuccess && <Alert tone="green">User created.</Alert>}
          <form
            className="form-grid"
            onSubmit={(e) => {
              e.preventDefault();
              create.mutate();
            }}
          >
            <Field label="New username">
              <Input
                required
                minLength={3}
                autoComplete="off"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
              />
            </Field>
            <Field label="Temporary password">
              <Input
                required
                minLength={12}
                type="password"
                autoComplete="new-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
            </Field>
            <Field label="Role">
              <Select value={role} onChange={(e) => setRole(e.target.value)}>
                <option value="viewer">Viewer</option>
                <option value="operator">Operator</option>
              </Select>
            </Field>
            <div className="form-actions">
              <Button disabled={create.isPending}>Create user</Button>
            </div>
          </form>
        </div>
      </Card>
      <Dialog
        open={enrollment !== null}
        onOpenChange={(open) => {
          if (!open) {
            setEnrollment(null);
            setCode("");
          }
        }}
        title="Enroll your authenticator"
        description="Add this account to a trusted TOTP authenticator, then enter its current code."
      >
        {enrollment && (
          <>
            <Field label="Authenticator setup secret">
              <Input
                readOnly
                value={String(enrollment.secret ?? "")}
                autoComplete="off"
              />
            </Field>
            <Field label="Provisioning URI">
              <Input
                readOnly
                value={String(enrollment.provisioning_uri ?? "")}
                autoComplete="off"
              />
            </Field>
            <p className="list-note">
              Enrollment expires {format.date(enrollment.expires_at)} IST. Keep
              this secret private.
            </p>
            <Field label="Six-digit authenticator code">
              <Input
                inputMode="numeric"
                autoComplete="one-time-code"
                value={code}
                onChange={(e) => setCode(e.target.value)}
              />
            </Field>
            {confirm.error && <Alert tone="red">{confirm.error.message}</Alert>}
            <div className="form-actions">
              <Button
                disabled={confirm.isPending || !code}
                onClick={() => confirm.mutate()}
              >
                Confirm enrollment
              </Button>
            </div>
          </>
        )}
      </Dialog>
      <Dialog
        open={recoveryCodes.length > 0}
        onOpenChange={(open) => {
          if (!open) setRecoveryCodes([]);
        }}
        title="Save your recovery codes"
        description="Each code works once. This list will be cleared when you close the dialog."
      >
        <div className="recovery-codes">
          {recoveryCodes.map((value) => (
            <code key={value}>{value}</code>
          ))}
        </div>
        <div className="form-actions">
          <Button onClick={() => setRecoveryCodes([])}>
            I have saved these codes
          </Button>
        </div>
      </Dialog>
      <Dialog
        open={confirmation !== null}
        onOpenChange={(open) => {
          if (!open) setConfirmation(null);
        }}
        title={confirmation?.title ?? "Confirm security change"}
        description="The server checks your role and recent identity verification before applying this change."
      >
        {action.error && <Alert tone="red">{action.error.message}</Alert>}
        <div className="form-actions">
          <Button variant="secondary" onClick={() => setConfirmation(null)}>
            Cancel
          </Button>
          <Button
            variant="danger"
            disabled={action.isPending}
            onClick={() => {
              if (confirmation)
                action.mutate({
                  path: confirmation.path,
                  method: confirmation.method,
                  body: confirmation.body,
                });
            }}
          >
            Confirm change
          </Button>
        </div>
      </Dialog>
    </div>
  );
}
