"""Reproducible validation reports; selection never reads out-of-sample data."""

from __future__ import annotations

import random
from datetime import datetime, timedelta
from decimal import Decimal
from itertools import product
from typing import Literal

from .arithmetic import fixed_decimal
from .backtest import BacktestConfig, BacktestEngine, BacktestMetrics, BacktestResult, EquityPoint, _hash, calculate_metrics
from .context import EvaluationContext, classify_regimes
from .models import Candle, CoreModel, Instrument
from .strategy import StrategyConfig

D = Decimal
RESEARCH_VERSION = "research-v1"


class ValidationRun(CoreModel):
    label: str
    strategy: StrategyConfig
    result: BacktestResult


class WalkForwardWindow(CoreModel):
    training_start: datetime
    training_end: datetime
    testing_start: datetime
    testing_end: datetime
    selected_configuration_hash: str
    training_candidates: tuple[ValidationRun, ...]
    out_of_sample: ValidationRun


class ValidationReport(CoreModel):
    research_version: str = RESEARCH_VERSION
    kind: Literal["OOS", "WALK_FORWARD", "PARAMETER_STABILITY", "COST_STRESS"]
    reproducibility_hash: str
    runs: tuple[ValidationRun, ...] = ()
    windows: tuple[WalkForwardWindow, ...] = ()
    assumptions: tuple[str, ...]
    warnings: tuple[str, ...] = ()


def _slice_config(config: BacktestConfig, candles: list[Candle], start: datetime | None = None) -> BacktestConfig:
    beginning = candles[0].timestamp
    ending = candles[-1].timestamp+timedelta(seconds=candles[-1].timeframe_seconds)
    return config.model_copy(update={"funding_events":[event for event in config.funding_events if beginning <= event.timestamp < ending],
                                     "trade_start_timestamp":start})


def _report(kind: str, runs: list[ValidationRun], assumptions: tuple[str, ...],
            windows: list[WalkForwardWindow] | None = None, warnings: tuple[str, ...] = ()) -> ValidationReport:
    payload = {"research_version":RESEARCH_VERSION,"kind":kind,"runs":[run.model_dump(mode="json") for run in runs],
               "windows":[window.model_dump(mode="json") for window in windows or []],"assumptions":assumptions}
    return ValidationReport(kind=kind,reproducibility_hash=_hash(payload),runs=tuple(runs),windows=tuple(windows or []),
                            assumptions=assumptions,warnings=warnings)


def out_of_sample(
    candles: list[Candle], instrument: Instrument, strategy: StrategyConfig, config: BacktestConfig,
    *, split_index: int, warmup_bars: int = 1000, higher_timeframes: dict[int,list[Candle]] | None = None,
    evaluation_contexts: dict[datetime,EvaluationContext] | None = None,
) -> ValidationReport:
    if not 2 <= split_index <= len(candles)-2 or warmup_bars < 0:
        raise ValueError("OOS split needs at least two bars in each period and nonnegative warmup")
    train,test = candles[:split_index],candles[max(0,split_index-warmup_bars):]
    engine = BacktestEngine()
    runs = [ValidationRun(label="IN_SAMPLE",strategy=strategy,result=engine.run(
        train,instrument,strategy,_slice_config(config,train),higher_timeframes=higher_timeframes,evaluation_contexts=evaluation_contexts)),
        ValidationRun(label="OUT_OF_SAMPLE",strategy=strategy,result=engine.run(
        test,instrument,strategy,_slice_config(config,test,candles[split_index].timestamp),
        higher_timeframes=higher_timeframes,evaluation_contexts=evaluation_contexts))]
    return _report("OOS",runs,("Pinned configuration; independent cash accounts for in-sample and out-of-sample.",
                               "OOS warmup can read earlier history; entry is blocked before the testing boundary.",
                               "First test opening may execute the last training closed-bar signal; no training positions carry."))


@fixed_decimal
def walk_forward(
    candles: list[Candle], instrument: Instrument, candidates: list[StrategyConfig], config: BacktestConfig,
    *, training_bars: int, testing_bars: int, step_bars: int | None = None, warmup_bars: int = 1000,
    minimum_training_trades: int = 3, higher_timeframes: dict[int,list[Candle]] | None = None,
    evaluation_contexts: dict[datetime,EvaluationContext] | None = None,
) -> ValidationReport:
    if not candidates or len(candidates) > 50 or training_bars < 2 or testing_bars < 2 or warmup_bars < 0:
        raise ValueError("walk-forward needs 1..50 candidates and valid window sizes")
    if len({(item.symbol,item.timeframe_seconds,item.version) for item in candidates}) != 1:
        raise ValueError("candidates must share instrument, timeframe and immutable source version")
    step = step_bars or testing_bars
    if step < testing_bars:
        raise ValueError("overlapping test windows are not supported")
    if minimum_training_trades < 1:
        raise ValueError("selection requires a positive minimum trade sample")
    window_count = max(0,(len(candles)-training_bars-testing_bars)//step+1)
    if window_count*len(candidates) > 500:
        raise ValueError("walk-forward exceeds 500-run budget")
    windows = []
    engine = BacktestEngine()
    for start in range(0,len(candles)-training_bars-testing_bars+1,step):
        boundary,end = start+training_bars,start+training_bars+testing_bars
        training = candles[start:boundary]
        runs = [ValidationRun(label=f"CANDIDATE_{index}",strategy=candidate,result=engine.run(
            training,instrument,candidate,_slice_config(config,training),higher_timeframes=higher_timeframes,
            evaluation_contexts=evaluation_contexts)) for index,candidate in enumerate(candidates)]
        eligible = [run for run in runs if run.result.metrics.total_trades >= minimum_training_trades]
        if not eligible:
            raise ValueError("training window has no candidate with the required trade sample; no fallback optimization")
        # A risk-adjusted objective and deterministic tie-break avoid selecting
        # solely maximum historical profit. All candidate evidence is retained.
        selected = max(eligible,key=lambda run:(run.result.metrics.net_return/(1+run.result.metrics.max_drawdown_fraction),
                                                -run.result.metrics.max_drawdown_fraction,
                                                _hash(run.strategy.model_dump(mode="json"))))
        testing = candles[max(start,boundary-warmup_bars):end]
        result = engine.run(testing,instrument,selected.strategy,_slice_config(config,testing,candles[boundary].timestamp),
                            higher_timeframes=higher_timeframes,evaluation_contexts=evaluation_contexts)
        windows.append(WalkForwardWindow(training_start=training[0].timestamp,training_end=candles[boundary].timestamp,
                                         testing_start=candles[boundary].timestamp,testing_end=candles[end-1].timestamp,
                                         selected_configuration_hash=_hash(selected.strategy.model_dump(mode="json")),
                                         training_candidates=tuple(runs),out_of_sample=ValidationRun(
                                             label="OUT_OF_SAMPLE",strategy=selected.strategy,result=result)))
    if not windows:
        raise ValueError("dataset does not contain a complete train/test window")
    return _report("WALK_FORWARD",[],(
        "Rolling chronological windows; selection uses only that window's training metrics.",
        "Objective = net return / (1 + maximum drawdown fraction), then lower drawdown, then configuration hash.",
        "Test windows never overlap; each resets capital and closes at its end; results are not compounded portfolio returns.",
        "All candidate evidence is saved; no eligible training sample means the report fails instead of inventing a winner.",
    ),windows)


def parameter_stability(
    candles: list[Candle], instrument: Instrument, strategy: StrategyConfig, config: BacktestConfig,
    *, parameters: dict[str,list[int|Decimal]], higher_timeframes: dict[int,list[Candle]] | None = None,
    evaluation_contexts: dict[datetime,EvaluationContext] | None = None,
) -> ValidationReport:
    allowed = {"fast_period","slow_period","lookback","rsi_period","rsi_lower","rsi_upper","stop_loss_pct",
               "take_profit_pct","risk_fraction","atr_stop_multiple","reward_risk_target"}
    if not parameters or not set(parameters) <= allowed or any(not values for values in parameters.values()):
        raise ValueError("stability grid requires nonempty supported parameter ranges")
    size = 1
    for values in parameters.values():
        size *= len(values)
    if size > 50:
        raise ValueError("stability grid exceeds 50 configuration budget")
    keys = sorted(parameters)
    runs = []
    for index,values in enumerate(product(*(parameters[key] for key in keys))):
        payload = strategy.model_dump()
        payload.update(dict(zip(keys,values)))
        candidate = StrategyConfig.model_validate(payload)
        runs.append(ValidationRun(label=f"GRID_{index}",strategy=candidate,result=BacktestEngine().run(
            candles,instrument,candidate,config,higher_timeframes=higher_timeframes,evaluation_contexts=evaluation_contexts)))
    return _report("PARAMETER_STABILITY",runs,(
        "Full bounded Cartesian grid, including unsuccessful configurations; no automatic promotion or optimum selection.",
        "Compare neighboring parameters, sample sizes, costs and drawdowns; this historical sensitivity is not predictive."))


def cost_stress(
    candles: list[Candle], instrument: Instrument, strategy: StrategyConfig, config: BacktestConfig,
    *, multipliers: tuple[Decimal,...] = (D(1),D(2),D(3)),
    higher_timeframes: dict[int,list[Candle]] | None = None,
    evaluation_contexts: dict[datetime,EvaluationContext] | None = None,
) -> ValidationReport:
    if not multipliers or len(multipliers)>10 or any(value < 1 for value in multipliers):
        raise ValueError("cost stress needs 1..10 multipliers >=1")
    runs = []
    for multiplier in multipliers:
        for category in ("fee_rate","spread_bps","slippage_bps","ALL_COSTS"):
            payload = config.model_dump()
            fields = ("fee_rate","spread_bps","slippage_bps") if category == "ALL_COSTS" else (category,)
            for field_name in fields:
                payload[field_name] *= multiplier
            stressed = BacktestConfig.model_validate(payload)
            runs.append(ValidationRun(label=f"{category}_X{multiplier}",strategy=strategy,result=BacktestEngine().run(
                candles,instrument,strategy,stressed,higher_timeframes=higher_timeframes,evaluation_contexts=evaluation_contexts)))
    return _report("COST_STRESS",runs,(
        "Re-executes the full strategy including sizing/rejections under one-cost and combined stress.",
        "Stress values are explicit scenarios, not estimates of historical spreads, fees or liquidity."))


class MonteCarloReport(CoreModel):
    research_version: str = RESEARCH_VERSION
    reproducibility_hash: str
    seed: int
    simulations: int
    mode: Literal["SHUFFLE", "BOOTSTRAP"]
    trade_count: int
    ending_equity_percentiles: dict[str,Decimal]
    max_drawdown_fraction_percentiles: dict[str,Decimal]
    ruin_threshold_fraction: Decimal
    ruin_probability: Decimal
    assumptions: tuple[str,...]


def _percentiles(values: list[Decimal]) -> dict[str,Decimal]:
    ordered = sorted(values)
    return {str(percentile):ordered[((len(ordered)-1)*percentile)//100] for percentile in (5,50,95)}


@fixed_decimal
def monte_carlo(result: BacktestResult, *, seed: int = 0, simulations: int = 1000,
                mode: Literal["SHUFFLE","BOOTSTRAP"] = "BOOTSTRAP",
                ruin_threshold_fraction: Decimal = D(".5")) -> MonteCarloReport:
    pnls = [trade.net_pnl for trade in result.trades]
    if not pnls or not 1 <= simulations <= 5000 or simulations*len(pnls) > 1000000:
        raise ValueError("Monte Carlo needs trades and a bounded 1..5000 simulations / million-draw budget")
    if mode not in ("SHUFFLE","BOOTSTRAP") or not 0 < ruin_threshold_fraction < 1:
        raise ValueError("invalid Monte Carlo mode or ruin threshold")
    rng = random.Random(seed)
    ending,drawdowns,ruined = [],[],0
    for _ in range(simulations):
        sequence = list(pnls)
        if mode == "SHUFFLE":
            rng.shuffle(sequence)
        else:
            sequence = [pnls[rng.randrange(len(pnls))] for _ in pnls]
        equity = peak = result.starting_capital
        drawdown = D(0)
        low = equity
        for pnl in sequence:
            equity += pnl
            peak = max(peak,equity)
            low = min(low,equity)
            drawdown = max(drawdown,(peak-equity)/peak)
        ending.append(equity)
        drawdowns.append(drawdown)
        ruined += low <= result.starting_capital*ruin_threshold_fraction
    payload = {"research_version":RESEARCH_VERSION,"run":result.reproducibility_hash,"seed":seed,
               "simulations":simulations,"mode":mode,"ruin_threshold_fraction":str(ruin_threshold_fraction)}
    return MonteCarloReport(reproducibility_hash=_hash(payload),seed=seed,simulations=simulations,mode=mode,
                            trade_count=len(pnls),ending_equity_percentiles=_percentiles(ending),
                            max_drawdown_fraction_percentiles=_percentiles(drawdowns),
                            ruin_threshold_fraction=ruin_threshold_fraction,ruin_probability=D(ruined)/simulations,
                            assumptions=("Trade net PnLs include recorded costs; sampled independently with fixed historic quantities.",
                                         "No rescaling, temporal dependence, changing market regime or execution path is reconstructed.",
                                         "Shuffle retains the same terminal profit and tests ordering only; bootstrap samples with replacement.",
                                         "Ruin = sampled path equity at/below the specified fraction of initial equity; this is model risk, not a probability forecast."))


class BenchmarkReport(CoreModel):
    strategy_net_return: Decimal
    cash_net_return: Decimal = D(0)
    buy_hold_net_return: Decimal
    strategy_minus_buy_hold: Decimal
    assumptions: tuple[str,...]


@fixed_decimal
def benchmark_comparison(result: BacktestResult, candles: list[Candle]) -> BenchmarkReport:
    from .execution import PaperFillModel, round_increment
    from .models import Side, validate_candles
    validate_candles(candles)
    if _hash([bar.model_dump(mode="json") for bar in candles]) != result.dataset_hash:
        raise ValueError("benchmark dataset must match the backtest identity")
    model = PaperFillModel(fee_rate=result.execution.fee_rate,spread_bps=result.execution.spread_bps,
                          slippage_bps=result.execution.slippage_bps)
    start,end = candles[0].open,candles[-1].close
    price = model.execution_price(result.instrument,Side.BUY,start)
    unit_cost = result.instrument.notional(price,D(1))*(1+model.fee_rate)
    quantity = round_increment(result.starting_capital/unit_cost,result.instrument.quantity_step)
    if result.instrument.max_quantity is not None:
        quantity = min(quantity,result.instrument.max_quantity)
    net = D(0)
    if quantity >= result.instrument.min_quantity:
        entry,exit_fill = model.fill(result.instrument,Side.BUY,quantity,start),model.fill(result.instrument,Side.SELL,quantity,end)
        payment = sum((result.instrument.notional(event.mark_price,quantity)*event.rate for event in result.execution.funding_events if event.timestamp>candles[0].timestamp),D(0))
        net = result.instrument.pnl(entry.price,exit_fill.price,quantity,Side.BUY)-entry.fee-exit_fill.fee-payment
    benchmark = net/result.starting_capital
    return BenchmarkReport(strategy_net_return=result.metrics.net_return,buy_hold_net_return=benchmark,
                           strategy_minus_buy_hold=result.metrics.net_return-benchmark,
                           assumptions=("Cash benchmark is zero interest in settlement currency.",
                                        "Buy-and-hold is one fully funded linear-futures long using whole contracts, same costs and supplied funding.",
                                        "Benchmark is static exposure without liquidation/rebalancing; it is not spot/ETF total return."))


class CorrelationReport(CoreModel):
    symbols: tuple[str,...]
    aligned_return_samples: int
    matrix: dict[str,dict[str,Decimal|None]]
    assumptions: tuple[str,...]


@fixed_decimal
def portfolio_correlations(datasets: dict[str,list[Candle]], *, minimum_samples: int = 30) -> CorrelationReport:
    from .models import validate_candles
    if not 2 <= len(datasets) <= 20 or minimum_samples < 2:
        raise ValueError("correlation requires 2..20 instruments and at least two samples")
    observations = {}
    timeframe = None
    for symbol,bars in datasets.items():
        validate_candles(bars)
        if len(bars)<2:
            raise ValueError("correlation needs at least two candles per symbol")
        timeframe = timeframe or bars[0].timeframe_seconds
        if bars[0].timeframe_seconds != timeframe:
            raise ValueError("portfolio correlations require the same explicit timeframe")
        observations[symbol] = {current.timestamp:current.close/previous.close-1 for previous,current in zip(bars,bars[1:])}
    stamps = sorted(set.intersection(*(set(values) for values in observations.values())))
    symbols = tuple(sorted(datasets))
    matrix: dict[str,dict[str,Decimal|None]] = {symbol:{} for symbol in symbols}
    for left in symbols:
        for right in symbols:
            correlation = None
            if len(stamps)>=minimum_samples:
                x,y = [observations[left][stamp] for stamp in stamps],[observations[right][stamp] for stamp in stamps]
                xm,ym = sum(x,D(0))/len(x),sum(y,D(0))/len(y)
                covariance = sum(((a-xm)*(b-ym) for a,b in zip(x,y)),D(0))
                denominator = (sum(((a-xm)**2 for a in x),D(0))*sum(((b-ym)**2 for b in y),D(0))).sqrt()
                if denominator>0:
                    correlation = max(D(-1),min(D(1),covariance/denominator))
            matrix[left][right] = correlation
    return CorrelationReport(symbols=symbols,aligned_return_samples=len(stamps),matrix=matrix,
                             assumptions=("Pearson correlation of simple close returns using exact shared UTC candle timestamps.",
                                          "No forward fill, interpolation or invented missing observations; undefined variance/sample is null.",
                                          "Historical correlation can change and does not replace central exposure limits."))


@fixed_decimal
def regime_attribution(result: BacktestResult, candles: list[Candle]) -> dict[str,dict[str,Decimal|int]]:
    if _hash([bar.model_dump(mode="json") for bar in candles]) != result.dataset_hash:
        raise ValueError("attribution dataset must match the backtest identity")
    regimes = classify_regimes(candles,result.strategy.regime_config)
    by_time = {snapshot.timestamp:snapshot.primary for snapshot in regimes if snapshot is not None}
    groups: dict[str,dict[str,Decimal|int]] = {}
    for trade in result.trades:
        regime = by_time.get(trade.signal_timestamp,"UNAVAILABLE")
        group = groups.setdefault(regime,{"trades":0,"net_pnl":D(0),"wins":0})
        group["trades"] += 1
        group["net_pnl"] += trade.net_pnl
        group["wins"] += trade.net_pnl>0
    return groups


class PortfolioReport(CoreModel):
    research_version: str = RESEARCH_VERSION
    reproducibility_hash: str
    currency: str
    starting_capital: Decimal
    ending_equity: Decimal
    allocation_fractions: dict[str,Decimal]
    component_hashes: dict[str,str]
    equity_curve: tuple[EquityPoint,...]
    metrics: BacktestMetrics
    maximum_gross_exposure_multiple: Decimal
    maximum_margin_fraction: Decimal
    exposure_gate_breached: bool
    assumptions: tuple[str,...]
    warnings: tuple[str,...]


@fixed_decimal
def simulate_portfolio(
    runs: dict[str,BacktestResult], *, maximum_gross_exposure_multiple: Decimal | None = None,
    maximum_margin_fraction: Decimal | None = None,
) -> PortfolioReport:
    """Synchronize actual component ledgers with fixed, isolated capital sleeves.

    No rescaling or interpolation of monetary trades is performed. The caller
    sizes each input backtest to its chosen allocation before combining them.
    """
    if not 2 <= len(runs) <= 20:
        raise ValueError("portfolio simulation requires 2..20 component runs")
    runs = dict(sorted(runs.items()))
    if len({run.currency for run in runs.values()}) != 1:
        raise ValueError("portfolio components need identical settlement currency; no implicit FX")
    if maximum_gross_exposure_multiple is not None and maximum_gross_exposure_multiple <= 0:
        raise ValueError("portfolio gross-exposure gate must be positive")
    if maximum_margin_fraction is not None and not 0 < maximum_margin_fraction <= 1:
        raise ValueError("portfolio margin gate must be within (0,1]")
    timelines = [[point.timestamp for point in run.equity_curve] for run in runs.values()]
    if not timelines[0] or any(timeline != timelines[0] for timeline in timelines[1:]):
        raise ValueError("portfolio curves must have identical observed timestamps; no forward-fill or missing capital")
    initial = sum((run.starting_capital for run in runs.values()),D(0))
    ending = sum((run.ending_equity for run in runs.values()),D(0))
    curve = []
    gross_peak = margin_peak = D(0)
    for points in zip(*(run.equity_curve for run in runs.values())):
        equity = sum((point.equity for point in points),D(0))
        gross = sum((point.gross_exposure for point in points),D(0))
        margin = sum((point.margin_used for point in points),D(0))
        curve.append(EquityPoint(timestamp=points[0].timestamp,equity=equity,
                                 cash=sum((point.cash for point in points),D(0)),
                                 unrealized_pnl=sum((point.unrealized_pnl for point in points),D(0)),
                                 gross_exposure=gross,margin_used=margin))
        # Insolvency is an explicit breach; undefined ratios are not rendered
        # as a favorable zero during negative-equity samples.
        gross_peak = max(gross_peak,gross/equity if equity>0 else (gross/initial+1))
        margin_peak = max(margin_peak,margin/equity if equity>0 else (margin/initial+1))
    breached = any(point.equity<=0 for point in curve) or (
        maximum_gross_exposure_multiple is not None and gross_peak>maximum_gross_exposure_multiple
    ) or (maximum_margin_fraction is not None and margin_peak>maximum_margin_fraction)
    attributed_trades = sorted(((name,trade) for name,run in runs.items() for trade in run.trades),
                               key=lambda item:(item[1].exit_timestamp,item[0],item[1].signal_id))
    trades = [trade for _,trade in attributed_trades]
    payload = {"research_version":RESEARCH_VERSION,"components":{name:run.reproducibility_hash for name,run in runs.items()},
               "maximum_gross_exposure_multiple":str(maximum_gross_exposure_multiple),"maximum_margin_fraction":str(maximum_margin_fraction)}
    return PortfolioReport(reproducibility_hash=_hash(payload),currency=next(iter(runs.values())).currency,
                           starting_capital=initial,ending_equity=ending,
                           allocation_fractions={name:run.starting_capital/initial for name,run in runs.items()},
                           component_hashes={name:run.reproducibility_hash for name,run in runs.items()},equity_curve=tuple(curve),
                           metrics=calculate_metrics(initial,ending,trades,curve),maximum_gross_exposure_multiple=gross_peak,
                           maximum_margin_fraction=margin_peak,exposure_gate_breached=breached,
                           assumptions=("Capital sleeves are fixed at each supplied run's actual starting balance; no borrowed cross-sleeve capital.",
                                        "Component cash, PnL, costs, funding, margin and gross exposures are summed at identical observed bar closes.",
                                        "Exposure gates diagnose the simulated path; they do not retroactively suppress trades or produce a compliant re-optimized path.",
                                        "Concurrent trade streak metrics use deterministic exit timestamp/component/signal ordering."),
                           warnings=("Independent isolated sleeves do not model exchange netting, cross margin or a central shared risk allocator.",
                                     "No implicit FX, missing-observation interpolation, dynamic allocation or order-book cross-impact."))
