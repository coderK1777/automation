# TradeBot Pro

Delta Exchange India strategy research, simulated PAPER trading and a separately gated DEMO integration. Next.js, FastAPI, PostgreSQL and Redis support independent market/PAPER, DEMO and reporting/notification workers.

Implemented features include immutable strategy versions, multi-timeframe and regime filters, advanced execution simulation, statistical research, document review, attributed news blackouts, scheduled reports, Telegram integration, MFA and encrypted credentials. [PROGRESS.md](PROGRESS.md) records acceptance evidence and remaining requirements. The original request is preserved in [docs/requirements.md](docs/requirements.md).

Live order routing is unavailable. No demo signing credentials or Telegram token are configured, so real private execution and delivery remain unverified. Local checks do not certify production readiness or profitability.

## Get the code and open VS Code

```bash
git clone https://github.com/coderK1777/automation.git tradebot-pro
cd tradebot-pro
code tradebot-pro.code-workspace
```

You can also use GitHub's **Code → Download ZIP**, extract it, and open the
workspace file. See [OPEN-IN-VSCODE.md](OPEN-IN-VSCODE.md). Credentials, local
databases, dependency installations and builds are excluded from the repository.
The verified runtime is Linux; VS Code editing also works on macOS/Windows.

## Cloud development startup

```bash
cd /workspace/automation
bash scripts/install.sh
bash scripts/dev.sh up
bash scripts/dev.sh status
.venv/bin/python scripts/check-readiness.py
```

Installation uses frozen Python/npm locks, preserves an existing ignored `.env`, initializes a private encrypted vault and builds the standalone web server. A missing `.env` gets randomized local credentials with mode `0600`. Read credentials only in a trusted local editor; keep them out of chat, Git and browser variables.

Startup migrates and preserves the operator, account history and strategy versions. It starts database/cache plus five application processes. A fresh public-data worker may need one cycle before readiness passes. Operator MFA still applies when enrolled. Restart processes after cloud publication/restoration; preserve a current database snapshot and the original vault key.

The seven-service container workflow is:

```bash
.venv/bin/python scripts/build-containers.py all
.venv/bin/python scripts/compose-app.py up
```

The helper stops owned host processes, maps a non-root runtime UID so the owner-only vault stays readable, migrates and starts the app. It preserves database/cache volumes and TLS verification. Stop app containers with `compose-app.py down` before returning to host startup; avoid duplicate executors.

## Checks

```bash
.venv/bin/python -m pytest -q
.venv/bin/python scripts/check-postgres.py
.venv/bin/ruff check services packages tests scripts
npm run typecheck --prefix apps/web
npm test --prefix apps/web
npm run build --prefix apps/web
```

PostgreSQL tests use a disposable schema. `check-expanded-browser.py` uses a temporary database for mutating feature checks. `check-app.py` deliberately trades a virtual PAPER probe and belongs to functional acceptance, not normal startup. Private demo acceptance has an explicit credentialed runner outside setup/CI.

## Guides

| Topic | Documentation |
| --- | --- |
| Setup and architecture | [Development](docs/development.md), [API](docs/api.md), [Architecture](docs/architecture.md), [Database](docs/database.md) |
| Strategies and research | [Quantitative core](docs/quantitative-core.md), [Research/news/reports](docs/features/research-news-reports-telegram.md) |
| Historical and PAPER execution | [Backtesting](docs/backtesting.md), [Persisted execution](docs/persisted-paper-execution.md) |
| Exchange and security | [DEMO](docs/demo-trading.md), [Security controls](docs/security-controls.md), [Live readiness](docs/live-readiness.md) |
| Monitoring and recovery | [Market/monitoring](docs/market-data-and-monitoring.md), [VPS/offsite backups](docs/vps-deployment-and-offsite-backups.md), [Operations](docs/operations.md) |
| Acceptance | [Progress](PROGRESS.md), [Known limitations](docs/known-limitations.md) |

₹50,000 is a configurable planning example, separate from settlement-currency account balance. ₹500 per trade is a research objective, never a guarantee or a sizing requirement.
