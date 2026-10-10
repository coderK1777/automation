import hashlib
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from tradebot.models import Backtest, Instrument, Strategy
from tradebot.paper import core_instrument
from tradebot.repository import version


def research_inputs(db: Session, strategy: Strategy, limit=2000, dataset_id=None):
    from tradebot_core import Candle, StrategyConfig
    from tradebot.data_models import HistoricalDataset
    from tradebot.data_provenance import create_dataset, dataset_candles
    saved = version(db, strategy)
    if not saved.validated:
        raise ValueError("Validate this strategy version before backtesting")
    config = StrategyConfig.model_validate(saved.config)
    dataset = db.get(HistoricalDataset, dataset_id) if dataset_id else create_dataset(db, config.symbol, config.timeframe_seconds, limit)
    if dataset is None or dataset.symbol != config.symbol or dataset.timeframe_seconds != config.timeframe_seconds:
        raise ValueError("Dataset does not exist or does not match the strategy instrument/timeframe")
    if not dataset.complete:
        raise ValueError("Dataset has missing candles; import actual missing history before evaluation")
    def observations(identifier):
        return [Candle.model_validate({key: value for key, value in item.items() if key in ("timestamp", "timeframe_seconds", "open", "high", "low", "close", "volume")}) for item in dataset_candles(db, identifier)]
    candles = observations(dataset.id)
    if len(candles) < max(config.slow_period, config.lookback) + 2:
        raise ValueError("Import sufficient real closed historical candles before backtesting")
    instrument_row = db.scalar(select(Instrument).where(Instrument.symbol == config.symbol))
    if instrument_row is None:
        raise ValueError("Discover and verify instrument economics before backtesting")
    instrument = core_instrument(instrument_row)
    higher = {}
    provenance = {"base": dataset.id, "higher_timeframes": {}, "source": dataset.source,
                  "warnings": dataset.warnings}
    for dependency in config.higher_timeframes:
        higher_dataset = create_dataset(db, config.symbol, dependency.timeframe_seconds, limit)
        if not higher_dataset.complete:
            raise ValueError("Higher-timeframe dataset is incomplete; no invented bars are permitted")
        higher[dependency.timeframe_seconds] = observations(higher_dataset.id)
        provenance["higher_timeframes"][str(dependency.timeframe_seconds)] = higher_dataset.id
    return config, instrument, candles, higher, provenance, instrument_row


def run_backtest(db: Session, strategy: Strategy, initial_capital, limit=2000, *, dataset_id=None,
                 fee_rate="0.0005", spread_bps=1, slippage_bps=2, funding_events=None):
    from tradebot_core import BacktestConfig, BacktestEngine
    config, instrument, candles, higher, provenance, instrument_row = research_inputs(db, strategy, limit, dataset_id)
    dataset_hash = hashlib.sha256(json.dumps([value.model_dump(mode="json") for value in candles], sort_keys=True).encode()).hexdigest()
    run = Backtest(strategy_id=strategy.id, version=strategy.current_version, status="RUNNING", dataset_hash=dataset_hash)
    db.add(run)
    db.flush()
    execution = BacktestConfig(initial_capital=initial_capital, dataset_version=provenance["base"],
                               fee_rate=fee_rate, spread_bps=spread_bps, slippage_bps=slippage_bps,
                               funding_mode="PROVIDED" if funding_events else "EXPLICIT_ZERO",
                               funding_events=funding_events or [], execution_model=config.execution)
    from tradebot.research_api import evaluation_contexts
    contexts = evaluation_contexts(db, config, candles) if config.filters.event_filter_enabled or config.filters.maximum_absolute_funding_rate is not None else None
    result = BacktestEngine().run(candles, instrument, config, config=execution, higher_timeframes=higher, evaluation_contexts=contexts)
    run.status, run.result = "COMPLETED", result.model_dump(mode="json")
    run.result["provenance"] = provenance
    run.result["warnings"] += provenance["warnings"]
    if not instrument_row.verified:
        run.result["assumptions"] += ["PAPER model uses an internal minimum of one whole contract and conservative 50% margin (maximum 2x leverage).",
                                       "Actual Delta minimum size and initial-margin percentage/tier rules are not certified for exchange order execution."]
        run.result["warnings"] += ["Verified linear economics cover observed multiplier, tick, contract unit, and USD settlement; metadata_verified in the core result refers to the declared PAPER economic model."]
    strategy.status = "BACKTESTED"
    return run
