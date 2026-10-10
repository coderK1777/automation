# Open TradeBot Pro in VS Code

Clone the repository into a new folder:

```bash
git clone https://github.com/coderK1777/automation.git tradebot-pro
cd tradebot-pro
code tradebot-pro.code-workspace
```

Alternatively, use GitHub's **Code → Download ZIP**, extract it, and open
`tradebot-pro.code-workspace` using VS Code. Sign in to your GitHub account if
the repository requires authentication. If the `code` terminal command is
unavailable, use VS Code's **File → Open Folder** and choose the project folder.

The repository includes application code, tests, dependency locks,
infrastructure and documentation. Local credentials, vault/recovery keys,
databases, logs, installed dependencies and builds are excluded. A clone does
not contain the cloud workspace's operator password or trading history.

VS Code can edit this project on macOS, Windows or Linux. The verified runtime
is Linux; Windows users can use WSL. The host process manager uses Linux
`/proc`, and the cloud installer uses `/workspace` caches. See
[development setup](docs/development.md) and [deployment](docs/vps-deployment-and-offsite-backups.md)
before running the application on another machine.

PAPER is the default. Production private order routing remains unavailable;
[PROGRESS.md](PROGRESS.md) records verified behavior and remaining work.
