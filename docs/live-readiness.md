# Live readiness and authorization

Live trading is disabled. The application has no production private order route and no readiness result can activate it. `LIVE_EXECUTION_ENABLED=false` is enforced by configuration validation. DEMO execution and safety controls are implemented separately; changing a flag cannot establish production acceptance, recoverability or independent review.

The requested progression is historical research → internal PAPER → actual Delta India DEMO → separately authorized limited LIVE. Profitable simulated or demo results cannot bypass operational gates.

## Evidence gates

| Gate | Required evidence | Current status |
| --- | --- | --- |
| Exchange | Current official India endpoint/specification verification; product economics; terms/account eligibility | Eight actual public REST/WebSocket checks passed. Linear multiplier/tick/currencies verified; exchange minimum/margin tiers and account eligibility require credentialed/operator evidence. |
| Credentials | Production-only destination binding, least permissions, no withdrawals, applicable IP restrictions and rotation | Production credentials neither requested for PAPER nor validated. |
| Demo | Real balance, order/fill/cancel/private-stream/protection tests and disconnect recovery | Backend, secure gates and explicit acceptance runner implemented; actual private tests blocked by absent demo credentials. Offline mocks do not certify acceptance. |
| Reconciliation | Startup and post-outage account match, manual/external position policy, unknown-order resolution | Durable DEMO fills/cash ledger, external-exposure policy and UNKNOWN recovery implemented; actual exchange/outage convergence unverified. |
| Execution safety | One active executor, pending reservations, idempotent signals/fills, partial fills and verified protective stops | PAPER/DEMO safety and fault tests implemented. Generated bracket ownership and trailing lifecycle need actual demo verification; production engine is absent. |
| Risk | Reviewed strategy/version/account limits, fresh metadata/data, fees/funding, liquidation distance and emergency actions | DEMO exact environment/version/capability and signal-evidence gates, central account limits and declared risk-capped sizing implemented. Advanced DEMO policies, per-strategy attribution controls, correlation groups and maintenance tiers remain explicitly unsupported rather than silently ignored. Further live-specific risk tests and approval required. |
| Security | MFA, secure deployed sessions/HTTPS, RBAC, rate limiting, dependency scan, independent security review | MFA, sensitive reauthentication, session controls, encrypted local vault and security tests implemented. Public deployment configuration and independent review remain outstanding. |
| Operations | Monitored VPS, restart/reboot/restore drills, alert delivery, clock health, backups and incident runbooks | Containers, metrics, backup/restore and local drills implemented/tested. Actual VPS provisioning, offsite delivery, alert delivery and fresh-environment publication evidence remain pending. |
| Operator authorization | Recorded account, strategy version, instrument, allocation, duration and maximum exposure approval | No authorization granted. |

No row is satisfied merely by its documentation, a passing offline mock or a positive return.

The append-only readiness workspace records exact strategy versions, evidence hashes and review outcomes. Stage `DEMO` is operator authorization to run the isolated demo engine; it is distinct from `DEMO_APPROVED`, which requires actual credentialed acceptance evidence. Starting a strategy cannot grant that certification or production authorization. The current blocked private-test record is [demo-private-2026-10-09.json](validation/demo-private-2026-10-09.json).

Isolated daily/weekly/monthly DEMO reports and native-currency exports are implemented from observed equity, managed fills and the signed cash ledger. Missing boundaries and unverified funding attribution are visible coverage limits; saved reports do not substitute for credentialed order/protection acceptance. The exact supported strategy subset is documented in [demo-trading.md](demo-trading.md).

## Explicit limited-live process

After every preceding gate passes, prepare a concrete review record with evidence references and remaining accepted limitations. The operator separately authorizes a small capped deployment using verified minimum sizes and independent risk limits. Record that decision in append-only approval/audit history. Every material increase in exposure or allocation requires a new approval.

Enable one selected strategy rather than all strategies. Monitor real exchange positions, costs, protection and reconciliation continuously. Critical uncertainty automatically suspends new exposure. Authorization never promises a guaranteed exit price or profit.

Live activation is a separate task. Environment setup, publication, service startup, a readiness page or an AI agent must not perform it implicitly.
