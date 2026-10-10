# Run from your computer's Downloads folder

This guide starts a local PAPER dashboard. Docker Desktop must be installed and running (Docker Engine with the Compose plugin also works on Linux). Docker runs the Python, Node, database and worker components; you do not need separate Python or Node installations. Keep Docker running while using the app.

## Windows

Install and open [Docker Desktop](https://www.docker.com/products/docker-desktop/), using its WSL 2/Linux containers backend. You run this project from ordinary Windows PowerShell; you do not need to move the code into WSL.

Download the ZIP from GitHub, extract it into your Windows Downloads folder and rename the extracted `automation-main` folder to `tradebot-pro`. Then open PowerShell and run:

```powershell
Set-Location "$HOME\Downloads\tradebot-pro"
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run-local.ps1
```

`ExecutionPolicy Bypass` applies only to this launcher process; it does not change your computer's permanent execution policy. Review the script before running it. The launcher generates private local login credentials, builds containers, prepares a Linux vault volume, migrates the database and checks the connected dashboard and worker. Read the login fields from `.env` in a local editor after it succeeds. Vault keys and database storage live in Docker's Linux volumes; code and `.env` stay in your Downloads folder. Retain those volumes and the matching `.env`. This keeps private vault permissions intact on Windows filesystems.

If Git is already installed, cloning is another way to create the folder:

```powershell
git clone https://github.com/coderK1777/automation.git "$HOME\Downloads\tradebot-pro"
```

From that same project folder, stop or inspect the app with:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run-local.ps1 -Action stop
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run-local.ps1 -Action status
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run-local.ps1 -Action check
```

Do not switch an existing host vault installation into this workflow: the launcher refuses that migration. An existing database without its matching vault volume or `.env` requires recovery before startup. A configured HTTPS proxy requires `SSL_CERT_FILE` pointing to its trusted public CA bundle; the launcher retains verified build/runtime trust and resolves the proxy host. Ordinary home connections need no proxy configuration.

## Storage and verification

The cloud folder `/workspace/downloads/tradebot-pro` is separate from your computer's Downloads folder. Existing Linux development instructions remain in README.md. The launcher does not publish a public website or enable real-money trading. Keep `.env` and the vault/database volumes private and preserve them together; stopping the app does not remove storage. Database snapshot recovery remains a separate documented operation.

The first run downloads and builds containers. Ports 3000, 8000, 5432 and 6379 must be available on your computer. Use one checkout/process workflow per Compose project. The public-data worker may need a polling cycle before the launcher reports readiness. If MFA is enrolled, the existing MFA-aware readiness workflow still applies. Windows Desktop host integration must be checked on your computer; the PowerShell startup and Linux container behavior are checked separately in the cloud.
