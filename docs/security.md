# Security model

The application implements authentication, role checks, MFA, session management,
local encrypted credentials and audit. These controls do not constitute an
independent security review or proof of a deployed production boundary. Gated
DEMO execution is implemented; actual credentialed private acceptance remains
separate. Production order submission and live activation are unavailable.

## Authentication and sensitive controls

Passwords use Argon2. Browser sessions use opaque random tokens, hashed database
identifiers and HttpOnly, SameSite-strict cookies. Secure cookies are the default;
local HTTP setup explicitly disables the Secure flag on its local origin.
Mutations require CSRF tokens and an allowed origin. Operator and viewer roles
separate administrative writes from reads. Login attempts and password/MFA
verification attempts have persistent rate limits. Validation responses omit
rejected input values so passwords and tokens are not reflected.

TOTP follows RFC 6238 with a thirty-second counter and a bounded clock-drift
window. Successful counters are consumed under a database row lock; recovery
codes are random, hashed and single-use. Enrollment expires, and its secret and
initial recovery codes appear only in the authenticated enrollment/confirmation
responses. Once enabled, future logins require a second factor.

Sensitive DEMO enablement, contract/risk review, user administration and readiness
evidence require enabled MFA plus password and second-factor verification within
five minutes, bounded by session expiry. Password-only recent authentication
allows enrollment and does not authorize those operations. Codes cannot be
replayed from login into reauthentication; use a fresh counter or another
recovery code.

Users can revoke their own sessions and rotate their passwords. Password changes
revoke other sessions and clear recent authentication. Operators with recent MFA
can create users, change roles/access and revoke another user's sessions. Access
changes revoke affected sessions, and the last active operator cannot be disabled
or demoted. There is no remote unauthenticated password-recovery route. A trusted
host CLI prompts locally, records recovery and preserves enrolled MFA.

## Secret storage and rotation

Local setup generates randomized PostgreSQL, Redis and bootstrap credentials in
ignored `.env` with mode `0600`, preserving existing values. Do not paste that
file into chat or logs. No exchange keys are needed for public data or PAPER.

DEMO credentials can use secure raw environment variables or the local vault.
The vault CLI prompts without echo, so values do not enter shell history or
arguments. HTTP APIs expose credential metadata only, never exchange values,
signatures or decryption keys. Proxy-secret placeholders are not raw HMAC keys;
local signing requires a suitable secure runtime secret facility.

Fernet authenticated encryption protects vault entries and database MFA secrets.
Ciphertext is purpose-bound to the credential name or MFA user ID. Key and vault
files must be private regular files; unsafe permissions and symlinks are rejected.
Atomic writes, synchronization and an interprocess lock protect configuration.
Key rotation retains prior decryption keys for interrupted writes and stored MFA
records. Exchange-side API key replacement and revocation are separate operations.

The encryption key must be readable by the actual service UID while remaining
`0600`. Keep it on an encrypted volume or local secret-manager mount, with secure
recovery storage separate from database dumps. A process with both key and vault
access can decrypt credentials; host and secret-manager access remain part of
the trust boundary. See [security-controls.md](security-controls.md) for commands,
enrollment, recovery and rotation procedures.

Never place secret values in frontend code, `NEXT_PUBLIC_*` variables, repository
history, logs, research artifacts or evidence notes. Restrict exchange API keys
to necessary permissions and approved IPs; withdrawals must be disabled. DEMO
and production credentials are separate records and exact destinations. Merely
storing a production record does not create an available production executor.

## API, transport and external integrations

Typed and bounded inputs, same-origin/CSRF enforcement, safe validation output
and authenticated access apply on the backend. Security headers include
content-type sniffing and framing protections. The local API ignores untrusted
forwarded headers; the provided HTTPS edge configuration strips client forwarding
headers and supplies its own edge connection identity. Metrics belong on the
private monitoring network and are blocked at the configured public edge.

The frontend proxy runs server-side. Demo intents and authoritative exchange
snapshots use separate tables from PAPER cash and positions. A changed demo
credential fingerprint requires explicit account review. Credentialed controls
never treat a mocked test or strategy start as certified demo acceptance.

News fetching is limited to operator-authorized sources with HTTPS validation and
public-address checks. Telegram settings require chat/user allowlists; delivery
also requires the local enable flag. Its durable outbox and command handling are
separate from execution protection. Imported research material is data and cannot
run executable code or authorize trading.

## Deployment and independent checks

The repository provides unprivileged containers, private/loopback service binds,
HTTPS reverse-proxy configuration, monitoring configuration and encrypted backup
with explicit SFTP delivery tooling. Local tests or configuration files do not
prove certificates, firewall policy, host patches, scheduled offsite delivery,
external Telegram delivery or a real VPS deployment.

Use a dedicated unprivileged account, trusted HTTPS, restricted database/cache
access and separately secured backup/vault keys. Test restoring into an isolated
database and recovering its MFA key before relying on recovery. Verify the actual
cookie/origin scope, proxy identity handling, CSP/headers, account isolation and
log redaction on the deployment. Dependency and image scanning belong in release
review; a test suite or one scan does not substitute for an external security
assessment. TLS and artifact verification must remain enabled.

## Readiness and incidents

Review evidence is authenticated, MFA-protected and appended for an exact strategy
version. All required reviews and distinct `DEMO_APPROVED` evidence remain gates;
recording `PASS` cannot activate live trading. Responses continue to report
`live_enabled=false` and `activation_available=false`.

During an incident, block new exposure independently of the browser, retain audit
and order evidence, revoke sessions or rotate compromised credentials, and
reconcile before resuming. Closing positions is a separate authorized action;
blocking entries does not assert closure. Inspect
[operations.md](operations.md) and the exact affected account/environment.
