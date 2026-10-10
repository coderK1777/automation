# Security controls and local credentials

The security API provides password reauthentication, TOTP MFA, recovery codes,
session revocation, user administration and immutable readiness evidence.
Exchange credential values have no HTTP configuration or read endpoint. Live
activation remains unavailable even after every review gate passes.

## Initialize local encryption

Run these commands in a trusted deployment terminal, in the repository root:

```bash
.venv/bin/python -m tradebot.vault init
.venv/bin/python -m tradebot.vault set delta-demo
.venv/bin/python -m tradebot.vault status
```

The `set` command prompts without echoing credentials. Values never enter shell
arguments, shell history, application responses or logs. Other supported records
are `telegram` and `delta-production`; storing a production record does not
activate a production executor. Use exchange keys with the necessary minimum
permissions, withdrawal disabled and an approved IP restriction.

`TRADEBOT_VAULT_KEY_FILE` selects the local key ring; its default is
`.runtime/vault.key`. `TRADEBOT_VAULT_FILE` selects the encrypted credential file;
its default is `.runtime/credentials.vault`. Both must be regular files with
owner-only permissions. Symlink key files and group/world-readable files are
rejected. Files and temporary writes use mode `0600`, an interprocess lock and
atomic replacement with filesystem synchronization.

Fernet authenticated encryption protects each secret. MFA ciphertext is bound
to its user ID; credential ciphertext is bound to its credential name. Keep the
key ring on an encrypted volume or a local secret-manager mount. A process that
can read both the vault and key can decrypt credentials, so host access controls
and secret-manager policy remain part of the deployment boundary. The key must
be readable by the actual service UID, including unprivileged containers, while
remaining `0600`. Do not solve a container ownership mismatch by making it public.

Database backups contain encrypted MFA secrets. They do not contain the local
vault key. Securely retain the key ring separately in encrypted offsite recovery
storage and test restoring both. Losing it prevents verifying existing TOTP
enrollments. Existing key files are preserved by `init`.

## MFA and recent authentication

Authenticate normally, then `POST /api/v1/security/reauth` with the password.
Begin enrollment at `POST /security/mfa/enroll` and add the returned provisioning
URI or secret to an RFC 6238 authenticator. The enrollment secret is returned
only in that authenticated response; no later status endpoint returns it.
Enrollment expires after ten minutes. Confirm with a six-digit code at
`POST /security/mfa/confirm`. Store the eight one-time recovery codes securely;
only hashes remain in the database.

Sensitive operations require enabled MFA and a password plus TOTP or recovery
code verified within the preceding five minutes. This includes demo activation,
user administration and readiness evidence. Password-only reauthentication
allows enrollment, but does not authorize these operations. Reauthentication
expires with the session if the session expires sooner. Password verification
and MFA confirmation are limited to ten attempts per user in fifteen minutes.

TOTP accepts the current thirty-second counter and one adjacent counter for
clock drift. Each accepted counter is consumed under a database row lock.
Reusing a code for a second login or reauthentication is rejected; use the next
authenticator code or a different recovery code. Recovery codes are also
consumed once. Confirmation upgrades the existing password reauthentication,
so enrollment immediately authorizes sensitive controls without replaying its
confirmation code. Future logins require a second factor once MFA is enabled.

`GET /security/sessions` returns display identifiers, never session cookies,
CSRF tokens or stored token hashes. Users can revoke their own sessions. An
operator with recent MFA can revoke all sessions of another user, change roles,
disable users or create a viewer/operator account. The last active operator
cannot be disabled or demoted. Role or access changes revoke affected sessions.
Changing a password revokes other sessions and clears recent authentication.
Sensitive operations remain subject to the existing same-origin and CSRF checks.

## Rotation and password recovery

```bash
.venv/bin/python -m tradebot.vault rotate
.venv/bin/python -m tradebot.vault set delta-demo
.venv/bin/python -m tradebot.security_admin_cli reset-password operator
```

Vault encryption rotation rewraps credential entries and retains prior
decryption keys so interrupted rotations and encrypted MFA database records
remain readable. Publish the enlarged key ring to secure recovery storage after
rotation. Removing old decryption keys is deliberately unavailable until all
database MFA ciphertext and retained backups can be migrated and verified.

Exchange API key rotation is a separate exchange-side operation: create a
replacement key with the same restricted permissions, configure it interactively,
verify authenticated read access, and revoke the previous exchange key. The CLI
increments the credential generation but never prints either value. Restart
long-running clients that already loaded the previous credentials.

Trusted-host password recovery prompts for a new password, records an audit
event and revokes all existing sessions. It preserves MFA. Recovery requires
interactive access to the deployment terminal and its database environment;
there is no remote unauthenticated recovery endpoint.

## Readiness evidence

An operator can append a category, outcome, evidence path, SHA-256 digest and
notes at `/security/readiness/{strategy_id}/evidence` after recent MFA. Evidence
identifies an exact strategy version, is immutable, and does not carry forward
to edited versions. Repeated reviews remain visible; the latest outcome per
category controls its review gate. The recorded digest is an operator assertion;
the API does not download arbitrary evidence links or independently certify a
review.

Readiness also requires validation, backtesting, paper approval, an actual
`DEMO_APPROVED` record and enabled operator MFA. Recording a `PASS` note alone
cannot manufacture the demo-approval gate. A completed review is not live
authorization. Every response continues to report `live_enabled=false` and
`activation_available=false`.

Tests cover RFC 6238 vectors, replay and recovery-code rejection, recent-auth
expiry, role/CSRF/session boundaries, rate limits, version evidence, ciphertext
authentication and purpose binding, unsafe file permissions, symlinks and
rotation recovery. Credentialed exchange testing, an external security review,
production HTTPS and encrypted offsite recovery remain deployment checks.
