# Persisted PAPER execution policies

These policies operate on virtual money and persisted public candles. They never
submit exchange orders. A configured execution policy reserves the approved
entry's margin and risk, then waits for a subsequent completed candle. The
existing simple PAPER policy remains available without an execution policy.

## Limit orders, participation and partial fills

Limits require penetration past the limit by the modeled adverse execution
costs. A touch does not guarantee a fill. An optional participation fraction
caps filled contracts using the completed candle's volume, with its explicitly
selected `CONTRACTS` or `UNDERLYING` unit. These are declared research assumptions;
the simulator does not reconstruct the exchange queue, intrabar order book or
actual available liquidity.

The capacity and penetration observation becomes available at candle completion,
so the assigned fill timestamp is its close. Prices earlier in that candle
cannot activate protection on the new position. Entry-close stop, liquidation
and target checks use only that close. This matches the quantitative advanced
engine's completed-bar fill policy. Subsequent bars replay protection from a
durable cursor; a gap or uncertain missing bar blocks additional exposure.

Each intent has one persistent client ID. Partial fills have deterministic
IDs derived from that intent and execution candle, accumulate into one owned
position and resize its two protective orders to the actual remaining quantity.
The remaining entry reservation shrinks in proportion to filled quantity. A
restart resumes this identity and state. Several unprocessed entry opportunities
or missing continuity cancel the remaining intent; they do not invent entries
during worker downtime. Explicit cancellation, expiry, emergency blocking,
pausing and version changes release the remaining reservation while retaining
protection on already filled quantity.

Every tranche rechecks account and strategy limits, instrument economics, news
and required-source health, external filters and executor health. The check
includes actual positions, other pending orders and this order's still-unfilled
reservation. An adverse gap can therefore reject a tranche that fits the
individual trade limit but would exceed the combined portfolio or strategy
capital budget. Existing partial fills count as the same daily trade, while
their margin, financing and P&L remain part of strategy limits.

## Targets and trailing protection

Multiple targets use tick-rounded reward multiples and explicit fractions.
Position reductions preserve entry economics, record actual reduced quantity,
release the matching margin, resize protection and cancel remaining entry
quantity. Whole-contract rounding may defer a small leg; the final target closes
the remaining quantity. Replaying an already handled candle does not repeat a
target or fill.

Fixed, percentage, ATR and structure trailing use completed data and become
effective on the following candle. Stops move only toward greater protection.
Cost-inclusive break-even includes paid entry fees, attributed financing,
modeled exit fees, adverse spread/slippage and tick rounding. Disabling the
cost option produces a price-only break-even policy instead. Custom indicator,
opposite-signal, trend-invalidation and time exits are evaluated using the exact
configuration captured when the position opened. An ATR-based dynamic target
updates its persisted bracket only after the completed observation.

These protective orders are internal simulator records. They do not prove a
native exchange stop exists or eliminate the dependency on the worker and its
market history.

## Financing and isolated liquidation

Financing uses only explicitly attributed scheduled events. The HTTP scheduling
endpoint rejects already-effective events because reconstructing historical
quantity after reductions requires a separate reconciliation workflow. Each
position/event pair posts one idempotent ledger movement. A manual close applies
due events before closing. A modeled historical exit applies events only through
its assigned execution time, so financing after that exit is never charged.

Intrabar protection uses financing known at bar open. A later event recognized
at the completed close can trigger maintenance at that close, but does not
retroactively change the earlier low or high. The simulator's assigned protection
times are explicit OHLC conventions, not observed exchange fill timestamps.

Optional isolated liquidation solves an internal linear maintenance equation
using captured contract economics, configured maintenance and liquidation fee
rates, current isolated margin and financing allocated to remaining lots.
Entry margin must cover the configured maintenance and liquidation charge.
Partial reductions release the corresponding allocation of old financing;
new events apply to the actual remaining quantity. Total financing remains in
the account ledger independently of this margin allocation.

A gap beyond liquidation takes precedence over an unavailable protective stop.
Within a continuous declared candle path, the nearer loss-side threshold wins.
Liquidation fees have a separate durable movement and an equity snapshot after
the charge. Neither this equation nor the internal margin model certifies Delta
cross-margin, exchange maintenance tiers or actual liquidation behavior.

## Verification

The integration tests exercise penetrated versus touched limits, partial fills
across database sessions, exact protection after cancellation, entry version and
health gates, zero-volume expiry, same-candle causal protection, multiple targets,
next-bar cost-inclusive break-even, financing before and after reductions, and
gap/threshold liquidation precedence. Financial fault tests also verify combined
reservation risk, source-health changes after queuing, due manual-close financing
and financing after a historical replayed exit. All use explicitly declared
synthetic fixtures in isolated test databases; they are not private-exchange
acceptance evidence.
