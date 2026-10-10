# Backtesting guide

Backtesting is historical simulation, visibly separate from PAPER and exchange trading. The API stores a strategy version, dataset identity, assumptions and results. Its output cannot authorize live execution.

## Inputs and reproducibility

Use a validated strategy version and an explicit instrument/timeframe dataset. Historical imports come from the read-only Delta India adapter when network access is available. Offline test fixtures are labeled synthetic test inputs and never presented as exchange history.

Record dataset hash, configuration, fees, slippage, capital, contract metadata and calculation conventions with every result. Decimal contract math must distinguish quantity, multiplier, notional, margin and settlement currency. The reference ₹50,000 setting is separate from actual simulated settlement-currency balance; currency conversion requires an explicit rate.

## Execution conventions

Signals use completed candles only and entries occur on later eligible data, preventing same-bar look-ahead. Stops and targets use declared intrabar rules. If a candle crosses both, choose a conservative documented outcome rather than the better result. Slippage and fees apply to both entries and exits. Gaps can execute away from the desired stop.

Review the formulas in [quantitative-model.md](quantitative-model.md) and the advanced policies in [quantitative-core.md](quantitative-core.md). The full-fill baseline remains available. Advanced simulation supports market/penetrated-limit entries, declared volume participation, partial fills and expiry, staged reward/risk targets, trailing and cost-inclusive break-even, time/indicator/trend exits, supplied funding and an explicitly configured isolated liquidation model. These are internal research assumptions. They do not certify exchange liquidity, queue priority, latency, cross margin or native protective-order semantics. Unavailable historical funding and bid/ask data are disclosed as missing/approximated.

Limit and volume-capped fills use completed-bar observations and receive bar-close timestamps. Whole-bar volume never authorizes a bar-opening fill. Protection for those fills starts at that close, without replaying the earlier high/low. Trailing and break-even derived from a completed candle become effective on the following candle and never loosen the existing stop. A partial protective exit cancels an unfilled entry remainder. Individual target quantities round down to whole increments; the final target closes the remaining position.

Higher-timeframe dependencies are explicit datasets. A dependency becomes available only after its close and must be warm, contiguous and current. Missing/open/stale dependency data blocks the signal. Regimes and external event/funding filters apply at the same decision time, with observation/publication timestamps. A missing required external feed blocks entry; it is never silently converted into an empty calendar.

## Review process

Inspect trade history, equity curve, drawdown, costs and sample size before approving PAPER. Undefined ratios (for example no losses in a tiny sample) must not be reported as universal excellence. A strategy earning money on one dataset is not an operational safety check.

The `tradebot_core.research` module implements chronological out-of-sample splits, rolling walk-forward selection, bounded parameter grids, independent/combined fee-spread-slippage stress, seeded Monte Carlo shuffle/bootstrap, cash/fully funded futures benchmarks, causal regime attribution, exact-timestamp return correlations and synchronized portfolio capital sleeves. Every report preserves the underlying run identities and assumptions.

Walk-forward selection uses training data only, requires a minimum trade sample and ranks net return divided by one plus maximum drawdown, with deterministic ties. All candidates remain visible. Warmup history may precede the testing boundary, but no entry occurs before it. Test windows do not overlap; each begins with independent capital, so their returns are not presented as a compounded portfolio. Monte Carlo samples recorded net trade P&Ls at historical quantities, without reconstructing dependence, changing size or market regimes. Portfolio sleeves sum actual equal-timestamp ledgers in the same currency; they do not simulate shared exchange netting or cross margin. Exposure gates diagnose a breach and do not rewrite prior simulated trades.

Monthly results include partial first/last months. Deployed-capital return divides net profit by mean sampled margin, and exposure-adjusted return divides net return by the fraction of bar-close samples with exposure. These are explicitly descriptive measures, not annualized forecasts. The ratios remain null without the needed denominator or complete daily sample.

Run automated core tests and backend integration tests before trusting a changed model. Verify a pinned version and dataset produce the same reproducibility identity and deterministic results. Do not modify previous stored results to match new code.
