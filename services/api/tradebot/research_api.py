"""Bounded historical validations over pinned strategy and dataset versions."""
import hashlib
import json
from datetime import timedelta
from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from tradebot.backtesting import research_inputs
from tradebot.db import aware, get_db
from tradebot.expansion_models import ResearchRun
from tradebot.models import Strategy
from tradebot.repository import audit, serialize
from tradebot.security import identity, operator

router = APIRouter(prefix="/api/v1/research/runs", tags=["quantitative validations"])


class RunInput(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_default=True)
    strategy_id: int = Field(gt=0)
    kind: Literal["OUT_OF_SAMPLE", "WALK_FORWARD", "STABILITY", "COST_STRESS", "MONTE_CARLO", "BENCHMARK", "REGIME", "PORTFOLIO"]
    initial_capital: Decimal = Field(default=10000, gt=0, le=100000000)
    limit: int = Field(default=2000, ge=50, le=5000)
    dataset_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    parameters: dict[str, Any] = Field(default_factory=dict)


def _number(parameters, key, default, minimum, maximum):
    value = parameters.get(key, default)
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{key} must be an integer between {minimum} and {maximum}")
    return value


def evaluation_contexts(db, config, candles):
    """Historical filters require evidence that was received by each decision."""
    from tradebot.news_models import NewsEvent, NewsSource, NewsSourceObservation
    from tradebot.expansion_models import PaperFundingEvent
    from tradebot_core.context import EconomicEvent, EvaluationContext
    sources = list(db.scalars(select(NewsSource).where(NewsSource.enabled.is_(True))))
    events = list(db.scalars(select(NewsEvent).where(NewsEvent.source_id.in_([source.id for source in sources])))) if sources else []
    observations = list(db.scalars(select(NewsSourceObservation).where(NewsSourceObservation.source_id.in_([source.id for source in sources]),
                         NewsSourceObservation.status == "SUCCESS"))) if sources else []
    funding = list(db.scalars(select(PaperFundingEvent).where(PaperFundingEvent.symbol == config.symbol).order_by(PaperFundingEvent.timestamp)))
    result = {}
    for candle in candles:
        at = candle.timestamp+timedelta(seconds=candle.timeframe_seconds)
        known = [event for event in events if aware(event.received_at) <= at and aware(event.published_at) <= at
                 and (event.superseded_at is None or aware(event.superseded_at) > at)
                 and (not event.symbols or config.symbol in event.symbols)]
        observed = max((aware(observation.observed_at) for observation in observations if aware(observation.observed_at) <= at), default=None)
        rates = [event for event in funding if aware(event.created_at) <= at and aware(event.timestamp) <= at]
        latest = rates[-1] if rates else None
        result[at] = EvaluationContext(events=tuple(EconomicEvent(event_id=str(event.id), timestamp=aware(event.scheduled_at or event.published_at),
                                           published_at=aware(event.published_at), impact=event.impact, source=str(event.source_id)) for event in known),
                                       event_feed_observed_at=observed,
                                       funding_rate=latest.rate if latest else None,
                                       funding_observed_at=aware(latest.timestamp) if latest else None)
    return result


@router.get("")
def runs(limit: int = Query(100, ge=1, le=200), db: Session = Depends(get_db), principal=Depends(identity)):
    return [serialize(row) for row in db.scalars(select(ResearchRun).order_by(ResearchRun.id.desc()).limit(limit))]


@router.get("/{identifier}")
def detail(identifier: int, db: Session = Depends(get_db), principal=Depends(identity)):
    run = db.get(ResearchRun, identifier)
    if run is None:
        raise HTTPException(404, "Research run not found")
    return serialize(run)


@router.post("", status_code=201)
def create(payload: RunInput, db: Session = Depends(get_db), user=Depends(operator)):
    from tradebot_core import BacktestConfig, BacktestEngine, StrategyConfig
    from tradebot_core import research
    strategy = db.get(Strategy, payload.strategy_id)
    if strategy is None:
        raise HTTPException(404, "Strategy not found")
    parameters = payload.parameters
    try:
        config, instrument, candles, higher, provenance, _ = research_inputs(db, strategy, payload.limit, payload.dataset_id)
        execution = BacktestConfig(initial_capital=payload.initial_capital, dataset_version=provenance["base"], execution_model=config.execution)
        contexts = evaluation_contexts(db, config, candles) if config.filters.event_filter_enabled or config.filters.maximum_absolute_funding_rate is not None else None
        common = {"higher_timeframes": higher, "evaluation_contexts": contexts}
        if payload.kind == "OUT_OF_SAMPLE":
            result = research.out_of_sample(candles, instrument, config, execution,
                                           split_index=_number(parameters, "split_index", len(candles)*2//3, 2, len(candles)-2),
                                           warmup_bars=_number(parameters, "warmup_bars", 1000, 0, 5000), **common)
        elif payload.kind == "WALK_FORWARD":
            candidate_values = parameters.get("candidates", [{}])
            if not isinstance(candidate_values, list) or not 1 <= len(candidate_values) <= 10:
                raise ValueError("candidates requires between one and ten parameter overrides")
            candidates = [StrategyConfig.model_validate({**config.model_dump(mode="json"), **value,
                                        "name": config.name, "version": config.version, "symbol": config.symbol,
                                        "timeframe_seconds": config.timeframe_seconds}) for value in candidate_values if isinstance(value, dict)]
            if len(candidates) != len(candidate_values):
                raise ValueError("Candidate overrides must be objects")
            train = _number(parameters, "training_bars", max(50, len(candles)//2), 2, 5000)
            test = _number(parameters, "testing_bars", max(20, len(candles)//4), 2, 5000)
            step = _number(parameters, "step_bars", test, test, 5000)
            estimated = max(0, (len(candles)-train-test)//step+1)*len(candidates)
            if estimated > 50:
                raise ValueError("Walk-forward API budget is fifty candidate runs")
            result = research.walk_forward(candles, instrument, candidates, execution, training_bars=train, testing_bars=test,
                       step_bars=step, warmup_bars=_number(parameters, "warmup_bars", 1000, 0, 5000),
                       minimum_training_trades=_number(parameters, "minimum_training_trades", 3, 1, 1000), **common)
        elif payload.kind == "STABILITY":
            grid = parameters.get("parameters", {"fast_period": [config.fast_period]})
            if not isinstance(grid, dict) or any(not isinstance(values, list) or len(values)>10 for values in grid.values()):
                raise ValueError("A bounded parameter grid is required")
            integer_fields = {"fast_period", "slow_period", "lookback", "rsi_period"}
            grid = {key: [int(value) if key in integer_fields else Decimal(str(value)) for value in values] for key, values in grid.items()}
            result = research.parameter_stability(candles, instrument, config, execution, parameters=grid, **common)
        elif payload.kind == "COST_STRESS":
            values = parameters.get("multipliers", [1, 2, 3])
            if not isinstance(values, list) or not 1 <= len(values) <= 5:
                raise ValueError("Cost stress needs one to five multipliers")
            result = research.cost_stress(candles, instrument, config, execution, multipliers=tuple(Decimal(str(value)) for value in values), **common)
        elif payload.kind == "PORTFOLIO":
            from tradebot.repository import candle_rows, core_candles
            symbols = parameters.get("symbols", [config.symbol])
            if not isinstance(symbols, list) or not 1 <= len(symbols) <= 10 or any(not isinstance(symbol, str) or not symbol.isalnum() for symbol in symbols):
                raise ValueError("Portfolio symbols must be one to ten instrument symbols")
            correlations = research.portfolio_correlations({symbol: core_candles(candle_rows(db, symbol, config.timeframe_seconds, payload.limit)) for symbol in symbols}) if len(symbols)>1 else None
            result = {"correlations": correlations.model_dump(mode="json") if correlations else None, "simulation": None,
                      "correlation_note": "At least two distinct instruments are required for inter-instrument correlations." if correlations is None else None}
            selected = parameters.get("strategy_ids", [])
            if selected:
                if not isinstance(selected, list) or not 2 <= len(selected) <= 10 or len(set(selected)) != len(selected) or any(type(identifier) is not int for identifier in selected):
                    raise ValueError("Portfolio simulation needs two to ten unique strategy IDs")
                allocations = parameters.get("allocations", {})
                if not isinstance(allocations, dict):
                    raise ValueError("Portfolio allocations must be an object keyed by strategy ID")
                weights = {identifier: Decimal(str(allocations.get(str(identifier), Decimal(1)/len(selected)))) for identifier in selected}
                if any(value <= 0 for value in weights.values()) or abs(sum(weights.values())-1) > Decimal("0.000000000001"):
                    raise ValueError("Portfolio capital allocation fractions must be positive and sum to one")
                inputs = {}
                for identifier in selected:
                    component = db.get(Strategy, identifier)
                    if component is None:
                        raise ValueError("Portfolio strategy does not exist")
                    values = research_inputs(db, component, payload.limit)
                    if values[0].timeframe_seconds != config.timeframe_seconds:
                        raise ValueError("Portfolio components require the same observed timeframe")
                    inputs[identifier] = values
                known = set.intersection(*(set(bar.timestamp for bar in values[2]) for values in inputs.values()))
                if len(known) < 50:
                    raise ValueError("Portfolio components need at least fifty common observed candles")
                components = {}
                for identifier, (component_config, component_instrument, component_bars, component_higher, component_provenance, _) in inputs.items():
                    aligned = [bar for bar in component_bars if bar.timestamp in known]
                    component_execution = execution.model_copy(update={"initial_capital":payload.initial_capital*weights[identifier],
                        "dataset_version":component_provenance["base"], "execution_model":component_config.execution})
                    components[str(identifier)] = BacktestEngine().run(aligned, component_instrument, component_config, component_execution,
                               higher_timeframes=component_higher, evaluation_contexts=evaluation_contexts(db, component_config, aligned))
                simulation = research.simulate_portfolio(components,
                             maximum_gross_exposure_multiple=Decimal(str(parameters.get("maximum_gross_exposure_multiple", 2))),
                             maximum_margin_fraction=Decimal(str(parameters.get("maximum_margin_fraction", ".5"))))
                result["simulation"] = simulation.model_dump(mode="json")
            else:
                result["warnings"] = ["Select at least two strategy_ids to execute a capital-allocated portfolio simulation; this result contains historical return correlations only."]
        else:
            base = BacktestEngine().run(candles, instrument, config, execution, **common)
            if payload.kind == "MONTE_CARLO":
                result = research.monte_carlo(base, seed=_number(parameters, "seed", 0, 0, 2147483647),
                           simulations=_number(parameters, "simulations", 1000, 1, 5000), mode=parameters.get("mode", "BOOTSTRAP"))
            elif payload.kind == "BENCHMARK":
                result = research.benchmark_comparison(base, candles)
            else:
                result = {"attribution": research.regime_attribution(base, candles),
                          "backtest_reproducibility_hash": base.reproducibility_hash,
                          "assumptions": ["Regimes use only observations known at each historical signal time."]}
        data = result.model_dump(mode="json") if hasattr(result, "model_dump") else json.loads(json.dumps(result, default=str))
    except (ValueError, TypeError, ArithmeticError) as error:
        raise HTTPException(409, str(error)) from error
    data["provenance"] = provenance
    identity_hash = hashlib.sha256(json.dumps({"datasets": provenance, "version": config.model_dump(mode="json"),
                              "kind": payload.kind, "parameters": parameters, "initial_capital": str(payload.initial_capital)}, sort_keys=True).encode()).hexdigest()
    data["request_reproducibility_hash"] = identity_hash
    run = ResearchRun(strategy_id=strategy.id, version=strategy.current_version, kind=payload.kind,
                      dataset_hash=provenance["base"], parameters=payload.model_dump(mode="json"), result=data)
    db.add(run)
    db.flush()
    audit(db, user.username, "RESEARCH_VALIDATION", str(run.id), {"kind": payload.kind, "hash": identity_hash})
    db.commit()
    return serialize(run)
