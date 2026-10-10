# Development and cloud environment setup

Use the existing `/workspace/automation` checkout and preserve its source changes, credentials and application history. This repository was empty/unborn when first inspected; inspect its actual Git HEAD and remote `main` rather than inferring publication from the prepared filesystem. Cloud tasks already use isolated environments, so a second worktree is unnecessary unless requested.

## Prerequisites and installation

Use the Python version in `.python-version`, frozen dependencies in `uv.lock`, and frontend Node/npm constraints plus `apps/web/package-lock.json`. Docker Compose provides PostgreSQL and Redis. The current cloud workflow requires a usable Docker daemon for those dependencies; shell-only installation does not establish service readiness.

Run from the repository root. Writable caches avoid a read-only home directory in managed cloud environments:

```bash
cd /workspace/automation
export UV_CACHE_DIR=/workspace/.cache/uv
export DOCKER_CONFIG=/workspace/.cache/docker
bash scripts/install.sh
```

The installer synchronizes frozen Python dependencies, preserves or creates owner-only `.env`, initializes the local vault without replacing existing keys, runs reproducible npm installation and the production frontend build, starts PostgreSQL/Redis, and prepares the inherited public CA bundle. It preserves TLS/package verification. Do not print `.env` values or supply credentials in chat, shell arguments or version control. Existing users/passwords are preserved by bootstrap.

## Host services

`bash scripts/dev.sh up` loads `.env` without evaluating shell content, starts PostgreSQL/Redis, restores a retained snapshot only into an empty database, upgrades Alembic, bootstraps the operator, initializes the vault, and starts five managed host processes:

| Process | Role |
| --- | --- |
| `api` | FastAPI on loopback port 8000, with forwarded proxy headers disabled. |
| `worker` | Durable PAPER execution/protection and central public market data. |
| `demo` | Isolated DEMO executor/private-stream coordinator; stays blocked without credentials and authorization. |
| `auxiliary` | News polling, completed-period reports and explicitly enabled Telegram delivery/read-only commands. |
| `web` | Built Next.js frontend on loopback port 3000. |

The web manager uses the standalone `server.js` build when present and copies its static assets into the standalone tree. It retains a normal Next start fallback for a compatible existing build. Backend rewrites use the build-time backend URL; rebuild when changing the backend destination.

Use `bash scripts/dev.sh status`, `restart` or `down`. Management affects only the recorded process groups; process fingerprints reject stale/reused PIDs and zombie states. PID records and logs stay in ignored `.runtime`. `/api/v1/health/ready` checks database/cache. Run `.venv/bin/python scripts/check-readiness.py` for authenticated, read-only application/worker checks. Ordinary startup does not submit exchange orders or run the intentional PAPER trade probe.

If the configured operator has enrolled MFA, use `check-readiness.py --mfa` in a trusted terminal for a hidden authenticator-code prompt. `--recovery` prompts for a one-use recovery code. Codes are not command-line arguments or saved environment values; normal MFA authentication remains enforced.

## Container services and vault ownership

Build with `.venv/bin/python scripts/build-containers.py all`, then start with `.venv/bin/python scripts/compose-app.py up`. The Compose stack has seven services: `postgres`, `redis`, `api`, `worker`, `demo`, `auxiliary` and `web`. The helper stops owned host processes, prepares owner-only vault mounts, restores a retained snapshot only into an empty database, applies migrations/bootstrap in the API image and starts the app profile. `compose-app.py status` inspects services; `down` stops the five application containers and retains database/cache volumes. Stop application containers before returning to host startup.

API/background images default to UID/GID 10001. The helper maps their runtime UID/GID to the unprivileged host file owner; when invoked as root it uses 10001 and prepares that owner explicitly. `.runtime/container-secrets` is 0700 and copied vault files are 0600, mounted read-only at `/run/tradebot-secrets`. This mapping allows the application to read its key without loosening secret permissions. The standalone frontend runs as the image's unprivileged `node` user. Images do not contain `.env`, runtime vaults or credentials.

Managed proxy setup resolves the actual proxy hostname and supplies the public CA bundle without disabling verification. Container migration and startup use the same verified trust route. Container vault files are copies: after configuring or rotating host vault credentials, rerun the preparation/start helper so the mounted copy is current. The Compose mount is read-only; perform vault administration from the trusted host CLI.

## Vault and sensitive controls

Vault initialization is safe to repeat:

```bash
.venv/bin/python -m tradebot.vault init
.venv/bin/python -m tradebot.vault status
```

Initialization preserves and validates an existing key; it refuses to create a replacement for an existing vault whose key is missing. Status prints names, generations and timestamps, never values. Host defaults are `.runtime/vault.key` and `.runtime/credentials.vault`; `TRADEBOT_VAULT_KEY_FILE` and `TRADEBOT_VAULT_FILE` select secure mounted locations. Protect original keys separately from database backups: encrypted credentials/MFA cannot be recovered with the database alone.

From an interactive trusted terminal, `.venv/bin/python -m tradebot.vault set delta-demo` prompts for hidden credential values. `set telegram` and `rotate` use the same local CLI. A production credential record does not enable production execution. DEMO writes additionally require explicit local `DEMO_EXECUTION_AUTHORIZED` authorization, enrolled MFA/recent authentication, contract review, reconciled account and healthy demo worker/private stream. Telegram delivery additionally requires its configured token, both allowlists and explicit delivery enablement. These external actions are separate from ordinary install/startup validation.

## Validation and reproducible artifacts

```bash
UV_CACHE_DIR=/workspace/.cache/uv uv run pytest
UV_CACHE_DIR=/workspace/.cache/uv uv run ruff check .
npm run typecheck --prefix apps/web
npm test --prefix apps/web
npm run build --prefix apps/web
.venv/bin/python scripts/check-postgres.py
.venv/bin/python scripts/check-readiness.py
```

Use browser, monitoring, public Delta and backup helpers when validating their corresponding changes. `scripts/check-app.py` deliberately creates/closes an internal PAPER functional probe; it is not a startup dependency. Read-only Delta/public-stream tests remain distinct from credentialed private DEMO acceptance. Keep SQLite/offline, actual PostgreSQL concurrency, browser/container and external connector evidence separate, and disclose skips. Current results are recorded in [PROGRESS.md](../PROGRESS.md).

`.venv/bin/python -m tradebot.cli export-openapi` regenerates `services/api/openapi.json` from the application. The CLI does not automatically load `.env`; securely load required environment values in the calling process, without shell evaluation or printing them. Frozen installs should preserve lockfiles; intentional dependency updates require refreshed locks/runtime exports.

Historical imports and immutable dataset snapshots, quantitative validation runs, document review, news/blackouts, PAPER reset/funding and report export are implemented management flows. They still require real observations, valid exact-version configuration and appropriate authorization. Generic health cannot replace those gates. Neither offline tests nor the local container stack establish actual private exchange execution or a secured VPS deployment.

## Snapshots and cloud configuration

Processes do not survive environment publication or a new task; restart using saved startup instructions and verify readiness. Docker named volumes may not survive publication. Retain a development database snapshot, restore only into an empty target, and preserve vault keys/credential records through an appropriate separate secret store. See [operations.md](operations.md) for restore, monitoring, encrypted offsite backup configuration and deployment prerequisites.

Public market work requires the validated India REST/WebSocket destinations; demo adds its India testnet hosts. News/Telegram/offsite providers need their own authorized network destinations. Preserve existing allowlists rather than replacing unknown settings. A saved configuration does not itself apply runtime connectivity or publish the environment.

Inspect only credential names/presence when diagnosing configuration. Platform secret placeholders cannot perform local HMAC signing; a signing process needs actual secret bytes supplied through its authorized secure mechanism. Native read-only Git access may work through the platform HTTPS proxy without a `GH_TOKEN`.

Save complete tested install/start instructions through cloud environment settings. Repository helper files alone do not register those instructions. Publication belongs to the user/product; distinguish saved draft configuration, current-instance checks and later published-instance validation.
