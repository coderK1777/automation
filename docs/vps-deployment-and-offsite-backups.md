# VPS deployment and encrypted offsite backups

The supplied overlays and scripts are deployable configuration. A configured public domain, actual VPS/firewall/DNS, an offsite destination and recovery credentials remain external requirements. No cloud host, certificate or remote backup delivery is invented by local tests. Keep production execution disabled until the separate live authorization and evidence gates pass.

## HTTPS edge

Use a dedicated runtime account, persistent storage and a synchronized system clock. Install Docker/Compose and provision least-privilege database/backup roles for the deployed environment. The development PostgreSQL bootstrap role is not a reviewed production privilege design. Expose TCP 80/443 and UDP 443 to the HTTPS edge; retain PostgreSQL, Redis, API, application web and monitoring ports on loopback/internal networks. Restrict SSH to authorized operators and retain an independent access/recovery path.

Configure `TRADEBOT_DOMAIN`, `TRADEBOT_ACME_EMAIL`, `APP_ORIGIN=https://your-domain` and `SESSION_COOKIE_SECURE=true` securely on the VPS. Create `.runtime/caddy/data` and `.runtime/caddy/config` owned by UID/GID 1000, matching the supplied unprivileged Caddy service. If the VPS runtime UID differs, adjust that service and directory ownership together. Check that the dedicated `172.30.88.0/24` edge network does not overlap an existing network before use.

After building/migrating the application, start:

```bash
docker compose -f docker-compose.yml -f infrastructure/docker-https.yml --profile app --profile https up -d
```

Caddy obtains and renews the certificate for the configured real domain, redirects HTTP to HTTPS, sets security headers and immediately flushes authenticated API streams. Metrics remain inside the monitoring boundary. The API trusts forwarded client identity only from the dedicated Caddy address `172.30.88.3`. Caddy replaces incoming forwarded headers with the actual connection identity. An arbitrary browser/Next.js client cannot provide a trusted login-rate-limit source address. The ordinary development stack continues to reject proxy headers.

The actual pinned Caddy image passed configuration validation locally. A real DNS/ACME/HTTPS acceptance check must still validate the deployed certificate, secure cookies, origin/CSRF rejection, forged forwarded-header rejection, streaming recovery, firewall exposure, reboot/restart and account reconciliation. Do not call local Caddy syntax validation a certificate/deployment test.

## Local backup evidence

`.venv/bin/python scripts/backup.py` writes a PostgreSQL custom-format dump through a private temporary file, verifies its signature, fsyncs it and atomically replaces the previous local snapshot only after success. Existing snapshots survive failed attempts. It records the actual last attempt and last success both in the private local status file and, when available, the database telemetry table. The monitoring API distinguishes a failed current attempt from an older successful backup.

The systemd service/timer under `infrastructure/systemd` supplies a daily 02:00 UTC schedule with a bounded randomized delay and missed-run recovery. Adjust the service account and `/opt/tradebot/automation` paths for the actual VPS. Install/enable the units using the host administrator's normal deployment process. The Docker socket grants powerful host capabilities; give the backup account only the host access the chosen deployment actually requires. A backup timer is not evidence that a restore succeeds.

## Encrypted artifacts

The backup helper uses streamed AES-256-GCM with a unique random nonce, authenticated format/content manifest and plaintext SHA-256. Encryption/decryption reads bounded chunks and supports large dumps without holding them in memory. Key files contain 32 random raw bytes, require mode 0600, are never accepted as plaintext arguments and never accompany an offsite artifact. Existing key/output/restore files are preserved. Tampering or a wrong key cannot publish a restored file.

Create and protect a recovery key once:

```bash
.venv/bin/python scripts/backup-offsite.py init-key --key-file .runtime/keys/backup.key
.venv/bin/python scripts/backup-offsite.py encrypt --key-file .runtime/keys/backup.key --output .runtime/backups/recovery.tbpenc
.venv/bin/python scripts/backup-offsite.py verify --key-file .runtime/keys/backup.key --input .runtime/backups/recovery.tbpenc
```

Store an independently protected recovery copy of the encryption key in the operator's secret manager. Losing the key loses access to encrypted backups. Do not store that recovery key beside the remote encrypted dumps. The ignored `.runtime` directory and key files must not enter source control or public artifacts. Set `BACKUP_ENCRYPTION_KEY_FILE` to the private file path for repeated operations; the file content stays out of configuration/API responses.

## Explicitly configured SFTP delivery

Configure the authorized destination metadata `BACKUP_SFTP_HOST`, `BACKUP_SFTP_USER`, `BACKUP_SFTP_DIRECTORY`, optional `BACKUP_SFTP_PORT`, `BACKUP_SFTP_IDENTITY_FILE` and `BACKUP_SFTP_KNOWN_HOSTS_FILE`. The identity file must be private. Verify the server host key through an independent authorized channel before provisioning `known_hosts`; never disable host-key checks or trust the first response automatically. Limit that remote account to its backup directory and retain enough storage/retention for the operator's recovery policy.

```bash
.venv/bin/python scripts/backup-offsite.py upload --key-file .runtime/keys/backup.key --input .runtime/backups/recovery.tbpenc
```

Upload refuses an incomplete destination. It authenticates the encrypted artifact locally, sends only ciphertext using noninteractive SFTP with strict host-key verification and one explicit identity, then renames the uploaded private temporary object into its final remote filename. Actual acknowledged delivery is recorded separately from local backup success. A failed or missing offsite transfer cannot be represented as a successful local dump or restore. Ordinary startup, backup and encryption do not transfer data to any external server.

The streaming encryption/restore and strict SFTP command contracts pass offline tests, including large binary round trips, wrong keys, ciphertext/tag tampering, overwrite prevention and corrupt-upload rejection. Actual offsite delivery requires the destination and identity configuration; it has not been performed without them.

The optional `tradebot-offsite-backup.service`/timer schedules delivery at 02:30 UTC, requires the local backup unit to finish first and invokes `scripts/backup-scheduled-offsite.py`. Install/enable it only after provisioning the authorized destination, verified host keys, private identity and separately recoverable encryption key. It fails closed when configuration or a recent local dump is missing; normal application startup never invokes it. Retain and monitor both local and offsite result evidence, and configure remote retention/capacity for the actual database size.

## Recovery drill

Download the chosen ciphertext using the same verified destination boundary. Verify/decrypt into a new private scratch file with `backup-offsite.py decrypt --input ... --output ... --key-file ...`. The helper never restores a database automatically. Restore the authenticated dump into an isolated PostgreSQL instance matching the supported version, verify migrations, constraints, users, immutable datasets, order/fill identities and account/position economics, and run the read-only readiness/fault checks without exchange orders. Document the restore time, data age and remaining recovery gaps before replacing any production state.

An independent host must be able to recover the database, encryption key, permitted credentials, application source/version and deployment configuration. Alert on local backup age/failure and offsite delivery age/failure, and test that the chosen operator receives the alert. Actual remote restore, VPS restart, load testing and limited-live authorization remain explicit acceptance evidence.
