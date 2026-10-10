"""Causal/financial edge cases using explicitly synthetic research fixtures."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from tradebot_core import (
    AdvancedExecutionConfig, BacktestConfig, BacktestEngine, Candle, EconomicEvent,
    EvaluationContext, ExternalFilters, FundingEvent, HigherTimeframeDependency,
    Instrument, Operand, PaperFillModel, PositionSizingConfig, ProtectionRules, RegimeConfig, RiskLimits, RiskContext,
    RuleNode, Side, StrategyConfig, StrategyEvaluator, TargetLeg, TradingSession,
    benchmark_comparison, classify_regimes, cost_stress, evaluate_strategy,
    isolated_liquidation_price, limit_fill, monte_carlo, next_protective_stop,
    out_of_sample, parameter_stability, portfolio_correlations, regime_attribution,
    simulate_portfolio, walk_forward, break_even_reference_price, size_position, evaluate_risk,
)

D = Decimal
T = datetime(2026,1,1,tzinfo=timezone.utc)


def bars(prices,tf=60,**changes):
    result = []
    for index,value in enumerate(prices):
        payload = dict(open=D(str(value)),high=D(str(value)),low=D(str(value)),close=D(str(value)),volume=D(30))
        payload.update(changes)
        if isinstance(value,dict):
            payload.update(value)
        result.append(Candle(timestamp=T+timedelta(seconds=index*tf),timeframe_seconds=tf,**payload))
    return result


def candle_series(values,**changes):
    result = []
    for index,value in enumerate(values):
        if isinstance(value,dict):
            p = D(str(value.get("close",100)))
            item = dict(open=p,high=p,low=p,close=p,volume=D(30))
            item.update(value)
        else:
            p = D(str(value))
            item = dict(open=p,high=p,low=p,close=p,volume=D(30))
        item.update(changes)
        result.append(Candle(timestamp=T+timedelta(minutes=index),**item))
    return result


def instrument(**changes):
    payload = dict(product_id=1,symbol="BTCUSD",underlying_asset="BTC",quote_asset="USD",settlement_asset="USD",
                   contract_size=D(".1"),tick_size=D(".01"),quantity_step=D(1),min_quantity=D(1),
                   initial_margin_rate=D(".1"),metadata_timestamp=T,metadata_verified=True)
    payload.update(changes)
    return Instrument(**payload)


def strategy(**changes):
    payload = dict(name="synthetic long",kind="CUSTOM",fast_period=2,slow_period=3,lookback=2,rsi_period=2,
                   stop_loss_pct=D(".1"),take_profit_pct=D(".5"),
                   long_rules=RuleNode(op="GT",left=Operand(feature="close"),right=Operand(value=D(0))))
    payload.update(changes)
    return StrategyConfig(**payload)


def config(**changes):
    payload = dict(initial_capital=D(1000),dataset_version="explicit-synthetic-v1",fee_rate=D(0),
                   spread_bps=D(0),slippage_bps=D(0))
    payload.update(changes)
    return BacktestConfig(**payload)


def run(values,cfg=None,st=None,ins=None):
    return BacktestEngine().run(candle_series(values),ins or instrument(),st or strategy(),cfg or config())


def test_higher_timeframe_bar_visible_only_after_close_and_future_revision_does_not_change_prefix():
    base = bars([100]*20)
    higher = bars([90,100,110,10],tf=300)
    cfg = strategy(higher_timeframes=[HigherTimeframeDependency(timeframe_seconds=300,fast_period=2,slow_period=3)])
    cache = StrategyEvaluator(cfg,base,higher_timeframes={300:higher})
    revised = StrategyEvaluator(cfg,base,higher_timeframes={300:higher[:-1]+bars([90,100,110,1000],tf=300)[-1:]})
    for index in range(14):
        assert cache.signal_at(index) is None
    assert cache.signal_at(14) is not None
    for index in range(14,19):
        assert cache.signal_at(index) == revised.signal_at(index)
        prefix = [bar for bar in higher if bar.timestamp+timedelta(seconds=300) <= base[index].timestamp+timedelta(seconds=60)]
        assert cache.signal_at(index) == evaluate_strategy(cfg,base[:index+1],higher_timeframes={300:prefix})
    assert cache.signal_at(19) is None
    assert revised.signal_at(19) is not None
    assert evaluate_strategy(cfg,base) is None


def test_missing_open_or_stale_higher_timeframe_and_invalid_dependencies_fail_closed():
    cfg = strategy(higher_timeframes=[HigherTimeframeDependency(timeframe_seconds=300,fast_period=2,slow_period=3)])
    higher = bars([90,100,110],tf=300)
    assert evaluate_strategy(cfg,bars([100]*21),higher_timeframes={300:higher}) is None
    open_higher = higher[:-1]+[higher[-1].model_copy(update={"closed":False})]
    assert evaluate_strategy(cfg,bars([100]*15),higher_timeframes={300:open_higher}) is None
    for dependency in ([{"timeframe_seconds":60}],[{"timeframe_seconds":90}],[{"timeframe_seconds":300},{"timeframe_seconds":300}]):
        with pytest.raises(ValidationError):
            strategy(higher_timeframes=dependency)


def test_regime_prefix_causality_explicit_thresholds_breakout_and_illiquidity():
    dataset = candle_series([100,100,100,110,111,100])
    settings = RegimeConfig(period=2,fast_period=2,slow_period=3,high_volatility=D(".03"),low_volatility=D(".001"))
    observed = classify_regimes(dataset,settings)
    for index in range(len(dataset)):
        assert observed[index] == classify_regimes(dataset[:index+1],settings)[-1]
    assert observed[0] is None
    assert observed[2].primary == "RANGE"
    assert observed[3].primary == "BREAKOUT"
    assert observed[3].volatility == "HIGH"
    illiquid = classify_regimes(dataset,settings.model_copy(update={"minimum_average_volume":D(31)}))
    assert illiquid[-1].primary == "ILLIQUID"
    assert evaluate_strategy(strategy(allowed_regimes=["RANGE"],regime_config=settings),dataset[:3]) is not None
    assert evaluate_strategy(strategy(allowed_regimes=["RANGE"],regime_config=settings),dataset[:4]) is None


def test_sessions_external_event_publication_and_funding_filters_are_explicit_and_fail_closed():
    session = TradingSession(start_hour_utc=22,end_hour_utc=2,weekdays=[3])
    assert session.allows(T)
    assert not session.allows(T+timedelta(hours=2))
    cfg = strategy(filters=ExternalFilters(event_filter_enabled=True,maximum_absolute_funding_rate=D(".001")))
    dataset = bars([100])
    assert evaluate_strategy(cfg,dataset) is None
    close = T+timedelta(minutes=1)
    ctx = EvaluationContext(event_feed_observed_at=T,funding_rate=D(".0001"),funding_observed_at=T)
    assert evaluate_strategy(cfg,dataset,context=ctx) is not None
    known = EconomicEvent(event_id="known",timestamp=close+timedelta(minutes=10),published_at=T,impact="HIGH",source="test-calendar")
    assert evaluate_strategy(cfg,dataset,context=ctx.model_copy(update={"events":(known,)})) is None
    future = known.model_copy(update={"published_at":close+timedelta(minutes=1)})
    assert evaluate_strategy(cfg,dataset,context=ctx.model_copy(update={"events":(future,)})) is not None
    assert evaluate_strategy(cfg,dataset,context=ctx.model_copy(update={"funding_rate":D(".002")})) is None
    assert evaluate_strategy(cfg,dataset,context=ctx.model_copy(update={"event_feed_observed_at":close+timedelta(seconds=1)})) is None


@pytest.mark.parametrize("feature",["wma","macd_line","macd_signal","macd_histogram","stochastic_k","stochastic_d",
                                  "donchian_upper","keltner_lower","supertrend","pivot","plus_di","adx","vwap"])
def test_all_indicator_rule_features_are_allowlisted_causal_and_warmup_safe(feature):
    cfg = strategy(long_rules=RuleNode(op="GT",left=Operand(feature=feature),right=Operand(value=D(-10000))))
    dataset = bars([100+i%5 for i in range(30)])
    cache = StrategyEvaluator(cfg,dataset)
    for index in range(len(dataset)):
        assert cache.signal_at(index) == evaluate_strategy(cfg,dataset[:index+1])
    assert cache.signal_at(29) is not None


def test_structure_and_atr_initial_stops_have_no_warmup_or_direction_shortcuts():
    cfg = strategy(stop_method="ATR",atr_stop_multiple=D(2),reward_risk_target=D(3))
    assert evaluate_strategy(cfg,bars([100])) is None
    signal = evaluate_strategy(cfg,candle_series([100,105,110]))
    assert signal.stop_price < signal.reference_price
    assert (signal.target_price-signal.reference_price)/(signal.reference_price-signal.stop_price) == 3
    structure = evaluate_strategy(strategy(stop_method="STRUCTURE"),candle_series([95,100,105]))
    assert structure.stop_price == 95
    assert evaluate_strategy(strategy(stop_method="FIXED_PRICE",stop_reference_price=D(200)),bars([100])) is None


def test_volume_partial_fills_expiry_and_cash_funding_reconcile_without_replaying_entry_bar():
    execution = AdvancedExecutionConfig(volume_participation_fraction=D(".1"),pending_expiry_bars=3)
    cfg = config(funding_mode="PROVIDED",funding_events=[FundingEvent(timestamp=T+timedelta(minutes=2),rate=D(".01"),mark_price=D(100),source="synthetic-funding")])
    result = run([100,{"open":D(100),"close":D(100),"high":D(120),"low":D(80)},100,100,110],cfg,strategy(execution=execution))
    trade = result.trades[0]
    assert trade.entry_timestamp == T+timedelta(minutes=2)
    assert trade.entry_fill_count == 3
    assert trade.quantity == 9
    assert trade.funding == D(".3")
    assert any(event.event == "EXPIRED" for event in result.order_events)
    assert trade.exit_reason == "END_OF_DATA"
    assert result.ending_equity-result.starting_capital == sum((item.net_pnl for item in result.trades),D(0))


def test_zero_volume_never_becomes_fabricated_partial_fill():
    result = BacktestEngine().run(candle_series([100]*5,volume=D(0)),instrument(),
                                 strategy(execution=AdvancedExecutionConfig(volume_participation_fraction=D(".1"))),config())
    assert not result.trades
    assert any(event.event == "EXPIRED" for event in result.order_events)


def test_limit_needs_penetration_and_never_executes_outside_limit():
    model = PaperFillModel(fee_rate=D(".001"),slippage_bps=D(10),spread_bps=D(10))
    assert limit_fill(instrument(),Side.BUY,D(1),D(99),candle_series([{"open":D(100),"high":D(100),"low":D(99),"close":D(100)}])[0],model) is None
    bar = candle_series([{"open":D(100),"high":D(101),"low":D(95),"close":D(100)}])[0]
    fill = limit_fill(instrument(),Side.BUY,D(1),D(99),bar,model)
    assert fill.price <= 99
    result = run([100,{"open":D(100),"high":D(101),"low":D(95),"close":D(100)},110],st=strategy(execution=AdvancedExecutionConfig(entry_type="LIMIT",limit_offset_fraction=D(".01"))))
    assert result.trades[0].entry_timestamp == T+timedelta(minutes=2)
    assert result.trades[0].entry_price == 99


def test_trailing_and_break_even_are_effective_next_bar_and_stops_never_loosen():
    cfg = strategy(exit_rules=ProtectionRules(trailing_method="FIXED",trailing_distance=D(5)))
    result = run([100,{"open":D(100),"high":D(120),"low":D(95),"close":D(115)},
                  {"open":D(114),"high":D(115),"low":D(113),"close":D(114)}],st=cfg)
    assert result.trades[0].exit_reason == "STOP_GAP"
    assert result.trades[0].exit_price == 114
    assert result.order_events[-1].event == "PROTECTION_UPDATED"
    be = run([100,{"open":D(100),"high":D(111),"low":D(95),"close":D(105)},
              {"open":D(105),"high":D(106),"low":D(99),"close":D(100)}],st=strategy(exit_rules=ProtectionRules(break_even_trigger_r=D(1))))
    assert be.trades[0].exit_price >= 100
    assert be.trades[0].exit_reason == "STOP"
    stop = next_protective_stop(side=Side.BUY,entry_price=D(100),initial_stop=D(90),current_stop=D(103),
                               favorable_price=D(105),candle=bars([104])[0],instrument=instrument(),rules=ProtectionRules(trailing_method="FIXED",trailing_distance=D(10)))
    assert stop == 103


def test_multi_target_partial_exits_and_stop_first_ambiguity():
    cfg = strategy(exit_rules=ProtectionRules(targets=[TargetLeg(reward_multiple=D(1),fraction=D(".5")),TargetLeg(reward_multiple=D(2),fraction=D(".5"))]))
    result = run([100,{"open":D(100),"high":D(111),"low":D(95),"close":D(110)},
                  {"open":D(110),"high":D(121),"low":D(105),"close":D(120)}],st=cfg)
    assert result.trades[0].exit_fill_count == 2
    assert result.trades[0].exit_price == 115
    assert result.trades[0].gross_pnl == 15
    ambiguous = run([100,{"open":D(100),"high":D(125),"low":D(80),"close":D(100)},100],st=cfg)
    assert ambiguous.trades[0].exit_reason == "STOP_FIRST_AMBIGUOUS"
    assert ambiguous.trades[0].net_pnl == -10


def test_time_exit_cancels_remaining_partial_entry_and_ledger_stays_exact():
    cfg = strategy(execution=AdvancedExecutionConfig(volume_participation_fraction=D(".1"),pending_expiry_bars=10),
                   exit_rules=ProtectionRules(max_holding_bars=2),cooldown_bars=10)
    result = run([100,100,100,105,105],st=cfg)
    assert result.trades[0].exit_reason == "TIME_EXIT"
    assert result.trades[0].quantity == 6
    assert any(event.event == "CANCELED" for event in result.order_events)
    assert result.ending_equity-result.starting_capital == result.trades[0].net_pnl


def test_isolated_liquidation_requires_explicit_model_and_gap_is_worse_than_unavailable_stop():
    assert isolated_liquidation_price(instrument=instrument(),side=Side.BUY,entry_price=D(100),quantity=D(10),
                                     isolated_margin=D(10),funding_paid=D(0),maintenance_margin_rate=D(".05")) == D("94.73684210526315789473684210526316")
    cfg = config(risk_limits=RiskLimits(max_leverage=D(10),max_gross_exposure_multiple=D(10)))
    st = strategy(max_leverage=D(10),stop_loss_pct=D(".2"),execution=AdvancedExecutionConfig(maintenance_margin_rate=D(".05")))
    result = run([100,100,{"open":D(70),"high":D(75),"low":D(65),"close":D(70)}],cfg,st)
    assert result.trades[0].exit_reason == "LIQUIDATION_GAP"
    assert result.trades[0].exit_price == 70
    with pytest.raises(ValueError,match="maintenance"):
        run([100,100],st=strategy(execution=AdvancedExecutionConfig(maintenance_margin_rate=D(".2"))))


def test_oos_warmup_has_no_training_trades_and_walk_forward_selection_cannot_read_test():
    dataset = candle_series([100,101,102,99,98,100,103,105,104,103,100,102,106,109,105,100])
    st = strategy(take_profit_pct=D(".2"),stop_loss_pct=D(".1"))
    oos = out_of_sample(dataset,instrument(),st,config(),split_index=8,warmup_bars=5)
    assert oos.runs[1].result.trades
    assert all(trade.entry_timestamp >= dataset[8].timestamp for trade in oos.runs[1].result.trades)
    one = walk_forward(dataset,instrument(),[st,st.model_copy(update={"take_profit_pct":D(".03")})],config(),
                       training_bars=8,testing_bars=4,minimum_training_trades=1)
    revised = dataset[:8]+candle_series([100,101,102,99,98,100,103,105,100,105,101,110,100,105,101,110])[8:]
    two = walk_forward(revised,instrument(),[st,st.model_copy(update={"take_profit_pct":D(".03")})],config(),
                       training_bars=8,testing_bars=4,minimum_training_trades=1)
    assert one.windows[0].selected_configuration_hash == two.windows[0].selected_configuration_hash
    assert one.windows[0].training_candidates == two.windows[0].training_candidates
    assert one.windows[0].out_of_sample.result != two.windows[0].out_of_sample.result
    assert one == walk_forward(dataset,instrument(),[st,st.model_copy(update={"take_profit_pct":D(".03")})],config(),training_bars=8,testing_bars=4,minimum_training_trades=1)


def test_stability_stress_monte_carlo_benchmark_and_regime_reports_use_saved_evidence():
    dataset = candle_series([100,102,99,103,98,104,100,103,99,104])
    st = strategy(take_profit_pct=D(".2"),stop_loss_pct=D(".1"))
    result = BacktestEngine().run(dataset,instrument(),st,config(fee_rate=D(".001")))
    stable = parameter_stability(dataset,instrument(),st,config(),parameters={"stop_loss_pct":[D(".1"),D(".2")],"take_profit_pct":[D(".2"),D(".3")]})
    assert len(stable.runs)==4
    stress = cost_stress(dataset,instrument(),st,config(fee_rate=D(".001")),multipliers=(D(1),D(2)))
    assert len(stress.runs)==8
    first = monte_carlo(result,seed=7,simulations=50,mode="SHUFFLE")
    assert first == monte_carlo(result,seed=7,simulations=50,mode="SHUFFLE")
    assert len(set(first.ending_equity_percentiles.values()))==1
    assert first.ending_equity_percentiles["50"] == result.ending_equity
    assert monte_carlo(result,seed=8,simulations=50).reproducibility_hash != first.reproducibility_hash
    benchmark = benchmark_comparison(result,dataset)
    assert benchmark.strategy_minus_buy_hold == result.metrics.net_return-benchmark.buy_hold_net_return
    attribution = regime_attribution(result,dataset)
    assert sum(group["trades"] for group in attribution.values()) == len(result.trades)
    assert sum((group["net_pnl"] for group in attribution.values()),D(0)) == result.metrics.net_profit
    assert result.metrics.monthly_returns["2026-01"] == result.metrics.net_return
    with pytest.raises(ValueError,match="match"):
        benchmark_comparison(result,dataset[:-1])


def test_correlations_use_exact_shared_times_and_return_null_for_inadequate_or_constant_samples():
    dataset = bars([100+i%5 for i in range(40)])
    observed = portfolio_correlations({"A":dataset,"B":dataset})
    assert observed.matrix["A"]["B"] == 1
    assert portfolio_correlations({"A":dataset,"B":bars([100]*40)}).matrix["A"]["B"] is None
    assert portfolio_correlations({"A":dataset[:5],"B":dataset[:5]}).matrix["A"]["B"] is None
    aligned = portfolio_correlations({"A":dataset,"B":dataset[5:]})
    assert aligned.aligned_return_samples == 34
    assert aligned.matrix["A"]["B"] == 1


def test_config_validation_and_environment_gates_do_not_silently_enable_execution():
    with pytest.raises(ValidationError):
        ProtectionRules(targets=[TargetLeg(reward_multiple=D(1),fraction=D(".6"))])
    with pytest.raises(ValidationError):
        ProtectionRules(trailing_method="PERCENT",trailing_distance=D(2))
    with pytest.raises(ValueError,match="permit"):
        run([100,100],st=strategy(allowed_environments=["PAPER"]))
    with pytest.raises(ValueError,match="budget"):
        parameter_stability(bars([100]*5),instrument(),strategy(),config(),parameters={"lookback":list(range(2,60))})
    with pytest.raises(ValueError,match="overlapping"):
        walk_forward(bars([100]*20),instrument(),[strategy()],config(),training_bars=8,testing_bars=4,step_bars=2)


@pytest.mark.parametrize("side",[Side.BUY,Side.SELL])
def test_break_even_reference_covers_costs_after_adverse_exit_and_rounding(side):
    model = PaperFillModel(fee_rate=D(".001"),slippage_bps=D(5),spread_bps=D(5))
    entry = model.fill(instrument(),side,D(10),D(100))
    reference = break_even_reference_price(instrument=instrument(),side=side,entry_price=entry.price,quantity=D(10),
                                          entry_fees=entry.fee,funding_paid=D(".1"),model=model)
    exit_fill = model.fill(instrument(),side.opposite,D(10),reference)
    assert instrument().pnl(entry.price,exit_fill.price,D(10),side)-entry.fee-exit_fill.fee-D(".1") >= 0


def test_synchronized_portfolio_adds_actual_ledgers_without_implicit_fx_or_interpolation():
    dataset = candle_series([100,100,105,110,115])
    first = BacktestEngine().run(dataset,instrument(),strategy(),config(initial_capital=D(1000)))
    second = BacktestEngine().run(dataset,instrument(),strategy(),config(initial_capital=D(2000)))
    portfolio = simulate_portfolio({"first":first,"second":second},maximum_gross_exposure_multiple=D(".01"))
    assert portfolio.starting_capital == 3000
    assert portfolio.ending_equity == first.ending_equity+second.ending_equity
    assert portfolio.metrics.net_profit == first.metrics.net_profit+second.metrics.net_profit
    assert portfolio.exposure_gate_breached
    assert portfolio == simulate_portfolio({"first":first,"second":second},maximum_gross_exposure_multiple=D(".01"))
    assert portfolio == simulate_portfolio({"second":second,"first":first},maximum_gross_exposure_multiple=D(".01"))
    assert all(point.equity == one.equity+two.equity for point,one,two in zip(portfolio.equity_curve,first.equity_curve,second.equity_curve))
    with pytest.raises(ValueError,match="currency"):
        simulate_portfolio({"first":first,"second":second.model_copy(update={"currency":"INR"})})
    with pytest.raises(ValueError,match="timestamps"):
        simulate_portfolio({"first":first,"second":second.model_copy(update={"equity_curve":second.equity_curve[:-1]})})


def test_indicator_exit_executes_at_later_open_and_future_edits_cannot_change_exit_signal():
    rules = RuleNode(op="GTE",left=Operand(feature="close"),right=Operand(value=D(105)))
    cfg = strategy(long_exit_rules=rules,cooldown_bars=10)
    result = run([100,100,105,106,110],st=cfg)
    assert result.trades[0].exit_reason == "INDICATOR_EXIT"
    assert result.trades[0].exit_timestamp == T+timedelta(minutes=3)
    assert result.trades[0].exit_price == 106
    dataset = bars([100,100,105,106,110])
    cache = StrategyEvaluator(cfg,dataset)
    revised = StrategyEvaluator(cfg,dataset[:-1]+bars([100,100,105,106,1])[-1:])
    assert cache.exit_at(2,Side.BUY) is True
    assert cache.exit_at(2,Side.BUY) == revised.exit_at(2,Side.BUY)


def test_short_trailing_monotonicity_and_costed_partial_fill_ledger():
    rules = ProtectionRules(trailing_method="PERCENT",trailing_distance=D(".05"))
    stop = next_protective_stop(side=Side.SELL,entry_price=D(100),initial_stop=D(110),current_stop=D(105),
                               favorable_price=D(90),candle=bars([94])[0],instrument=instrument(),rules=rules)
    assert stop == D("94.50")
    next_stop = next_protective_stop(side=Side.SELL,entry_price=D(100),initial_stop=D(110),current_stop=stop,
                                    favorable_price=D(95),candle=bars([94])[0],instrument=instrument(),rules=rules)
    assert next_stop == stop
    cfg = config(fee_rate=D(".001"),spread_bps=D(1),slippage_bps=D(2))
    result = run([100,100,100,100,102,105],cfg,strategy(execution=AdvancedExecutionConfig(
        volume_participation_fraction=D(".1"),pending_expiry_bars=5)))
    assert result.trades[0].entry_fill_count >= 2
    assert result.ending_equity-result.starting_capital == sum((trade.net_pnl for trade in result.trades),D(0))
    assert result.metrics.fees == sum((trade.fees for trade in result.trades),D(0))


@pytest.mark.parametrize("advanced",[False,True])
def test_strategy_daily_trade_loss_and_capital_allocation_limits_are_enforced(advanced):
    execution = AdvancedExecutionConfig() if advanced else None
    values = [100,{"open":D(100),"high":D(101),"low":D(80),"close":D(100)},100,100,100]
    count_limited = run(values,st=strategy(execution=execution,max_trades_per_day=1))
    assert len(count_limited.trades)==1
    assert any("STRATEGY_DAILY_TRADE_LIMIT" in item.reasons for item in count_limited.rejected_signals)
    loss_limited = run(values,st=strategy(execution=execution,max_daily_loss_fraction=D(".005")))
    assert len(loss_limited.trades)==1
    assert any("STRATEGY_DAILY_LOSS_LIMIT" in item.reasons for item in loss_limited.rejected_signals)
    allocated = run([100,100,100],st=strategy(execution=execution,capital_allocation_fraction=D(".01")))
    assert allocated.trades[0].quantity==2
    for value in (D(0),D("1.1")):
        with pytest.raises(ValidationError):
            strategy(capital_allocation_fraction=value)


def test_intrabar_funding_is_preserved_by_oos_slicing_and_conservative_when_exit_timing_unknown():
    event = FundingEvent(timestamp=T+timedelta(seconds=90),rate=D(".01"),mark_price=D(100),source="synthetic-intrabar")
    cfg = config(funding_mode="PROVIDED",funding_events=[event])
    st = strategy(execution=AdvancedExecutionConfig())
    values = [100,{"open":D(100),"high":D(101),"low":D(80),"close":D(100)},100,100,100,100]
    charged = run(values,cfg,st)
    assert charged.trades[0].funding==1
    assert charged.trades[0].net_pnl == -11
    assert any("Intrabar funding" in warning for warning in charged.warnings)
    credited = run(values,cfg.model_copy(update={"funding_events":[event.model_copy(update={"rate":D("-.01")})]}),st)
    assert credited.trades[0].funding==0
    report = out_of_sample(candle_series(values),instrument(),st,cfg,split_index=3,warmup_bars=2)
    assert report.runs[0].result.execution.funding_events == [event]
    assert report.runs[0].result.trades[0].funding==1


def test_walk_forward_budget_rejects_before_any_candidate_execution(monkeypatch):
    def fail(*args,**kwargs):
        raise AssertionError("budget validation must precede expensive execution")
    monkeypatch.setattr(BacktestEngine,"run",fail)
    with pytest.raises(ValueError,match="500-run budget"):
        walk_forward(bars([100]*1100),instrument(),[strategy()],config(),training_bars=2,testing_bars=2)


def test_buy_hold_benchmark_does_not_charge_funding_retroactively_at_entry_open():
    dataset = bars([100,100,100])
    event = FundingEvent(timestamp=T,rate=D(".01"),mark_price=D(100),source="synthetic-entry-funding")
    result = BacktestEngine().run(dataset,instrument(),strategy(),config(funding_mode="PROVIDED",funding_events=[event]))
    assert benchmark_comparison(result,dataset).buy_hold_net_return==0
    with pytest.raises(ValidationError,match="cannot be combined"):
        ProtectionRules(targets=[TargetLeg(reward_multiple=D(2),fraction=D(1))],dynamic_target_atr_multiple=D(2))


def sized(policy=None,**changes):
    values = dict(instrument=instrument(),side=Side.BUY,entry_price=D(100),stop_price=D(90),equity=D(1000),
                  risk_fraction=D(".01"),max_leverage=D(2),fee_rate=D(0),spread_bps=D(0),slippage_bps=D(0),sizing=policy)
    values.update(changes)
    return size_position(**values)


@pytest.mark.parametrize("policy,quantity",[
    (PositionSizingConfig(),D(10)),
    (PositionSizingConfig(mode="FIXED_CONTRACTS",fixed_quantity=D(5)),D(5)),
    (PositionSizingConfig(mode="FIXED_NOTIONAL",fixed_notional=D(50)),D(5)),
    (PositionSizingConfig(mode="VOLATILITY",volatility_multiplier=D(2)),D(2)),
])
def test_sizing_styles_request_quantities_and_cannot_bypass_cost_risk_or_margin(policy,quantity):
    result = sized(policy,volatility_atr=D(20))
    assert result.quantity==quantity
    assert result.sizing_mode==policy.mode
    assert result.risk_amount<=10
    assert sized(policy,volatility_atr=D(20),available_margin=D(5)).quantity<=1
    assert sized(policy,volatility_atr=D(20),max_notional=D(10)).quantity<=1
    assert sized(policy,volatility_atr=D(20),instrument=instrument(max_quantity=D(1))).quantity<=1
    costly = sized(policy,volatility_atr=D(20),fee_rate=D(".001"),spread_bps=D(2),slippage_bps=D(2))
    assert costly.risk_amount<=10
    assert costly.quantity<=quantity


def test_oversized_fixed_requests_and_small_atr_are_capped_by_same_stop_risk_budget():
    for policy in (PositionSizingConfig(mode="FIXED_CONTRACTS",fixed_quantity=D(1000000)),
                   PositionSizingConfig(mode="FIXED_NOTIONAL",fixed_notional=D(1000000)),
                   PositionSizingConfig(mode="VOLATILITY",volatility_multiplier=D(2))):
        result = sized(policy,volatility_atr=D(".1"))
        assert result.quantity==10
        assert result.sizing_capped
        assert sized(policy,volatility_atr=D(".1"),risk_budget=D(5)).quantity==5
    for value in (None,D(0),D(-1),D("NaN")):
        with pytest.raises(ValueError,match="ATR"):
            sized(PositionSizingConfig(mode="VOLATILITY"),volatility_atr=value)


def test_fixed_notional_uses_settlement_conversion_and_whole_lots_without_guessing_fx():
    converted = instrument(settlement_asset="INR",quote_to_settlement_rate=D(85),conversion_timestamp=T)
    result = sized(PositionSizingConfig(mode="FIXED_NOTIONAL",fixed_notional=D(4250)),instrument=converted,equity=D(85000))
    assert result.quantity==5
    assert result.notional==4250
    with pytest.raises(ValidationError,match="fixed_quantity"):
        PositionSizingConfig(mode="FIXED_CONTRACTS")
    with pytest.raises(ValidationError,match="fixed_notional"):
        PositionSizingConfig(mode="RISK_FRACTION",fixed_notional=D(100))


def test_correlated_notional_gate_accepts_exact_boundary_and_rejects_either_side_oversize():
    position_size = sized()
    limits = RiskLimits(max_correlated_exposure_multiple=D(".2"))
    context = RiskContext(equity=D(1000),available_margin=D(1000),day_start_equity=D(1000),week_start_equity=D(1000),peak_equity=D(1000),
                          now=T,market_timestamp=T,account_reconciled=True,instrument_metadata_fresh=True,data_healthy=True,
                          exchange_healthy=True,worker_healthy=True,clock_healthy=True,database_healthy=True,protective_orders_verified=True,
                          has_unknown_orders=False,correlated_exposure=D(100))
    options = dict(limits=limits,context=context,instrument=instrument(),side=Side.BUY,size=position_size,
                   target_price=D(120),leverage=D(2),fee_rate=D(0),spread_bps=D(0),slippage_bps=D(0))
    assert evaluate_risk(**options).approved
    options["context"] = context.model_copy(update={"correlated_exposure":D("100.01")})
    assert "CORRELATED_EXPOSURE_LIMIT" in evaluate_risk(**options).reasons
    options["side"] = Side.SELL
    options["size"] = sized(side=Side.SELL,stop_price=D(110))
    options["target_price"] = D(80)
    assert "CORRELATED_EXPOSURE_LIMIT" in evaluate_risk(**options).reasons
    options["limits"] = RiskLimits()
    assert evaluate_risk(**options).approved


@pytest.mark.parametrize("advanced",[False,True])
def test_backtests_apply_fixed_sizing_and_closed_bar_atr_without_lookahead(advanced):
    execution = AdvancedExecutionConfig() if advanced else None
    fixed = run([100,100,105],st=strategy(execution=execution,sizing=PositionSizingConfig(mode="FIXED_CONTRACTS",fixed_quantity=D(2))))
    assert fixed.trades[0].quantity==2
    dataset = candle_series([100,100,{"open":D(100),"high":D(110),"low":D(95),"close":D(105)},
                             {"open":D(105),"high":D(115),"low":D(100),"close":D(110)},110])
    config_strategy = strategy(execution=execution,sizing=PositionSizingConfig(mode="VOLATILITY",volatility_period=2,volatility_multiplier=D(2)))
    result = BacktestEngine().run(dataset,instrument(),config_strategy,config())
    assert any("ATR" in str(item.reasons) for item in result.rejected_signals)
    assert result.trades
    assert result.trades[0].entry_timestamp>=T+timedelta(minutes=3)
    revised = dataset[:-1]+[dataset[-1].model_copy(update={"open":D(1000),"high":D(1000),"low":D(1000),"close":D(1000)})]
    repeat = BacktestEngine().run(revised,instrument(),config_strategy,config())
    assert result.trades[0].quantity==repeat.trades[0].quantity
    assert result.trades[0].entry_price==repeat.trades[0].entry_price


def test_optional_strategy_loss_and_liquidation_settings_are_bounded_and_require_a_declared_model():
    with pytest.raises(ValidationError,match="maintenance"):
        strategy(min_liquidation_distance_fraction=D(".1"))
    accepted = strategy(max_consecutive_losses=3,max_drawdown_fraction=D(".05"),max_open_positions=1,
                        min_liquidation_distance_fraction=D(".1"),execution=AdvancedExecutionConfig(maintenance_margin_rate=D(".02")))
    assert accepted.max_consecutive_losses==3
    for field in ("max_consecutive_losses","max_drawdown_fraction","max_open_positions","min_liquidation_distance_fraction"):
        with pytest.raises(ValidationError):
            strategy(**{field:0})


@pytest.mark.parametrize("advanced",[False,True])
def test_strategy_consecutive_loss_and_drawdown_block_new_entries_using_existing_evidence(advanced):
    execution = AdvancedExecutionConfig() if advanced else None
    values = [100,{"open":D(100),"high":D(101),"low":D(80),"close":D(100)},100,100,100]
    streak = run(values,st=strategy(execution=execution,max_consecutive_losses=1))
    assert len(streak.trades)==1
    assert any("STRATEGY_CONSECUTIVE_LOSS_LIMIT" in str(item.reasons) for item in streak.rejected_signals)
    drawdown = run(values,st=strategy(execution=execution,max_drawdown_fraction=D(".005")))
    assert len(drawdown.trades)==1
    assert any("STRATEGY_DRAWDOWN_LIMIT" in str(item.reasons) for item in drawdown.rejected_signals)


def test_minimum_liquidation_distance_uses_declared_model_and_blocks_close_boundary():
    st = strategy(execution=AdvancedExecutionConfig(maintenance_margin_rate=D(".05")),min_liquidation_distance_fraction=D(".6"))
    blocked = run([100,100,100],st=st)
    assert not blocked.trades
    assert any("LIQUIDATION_DISTANCE_LIMIT" in str(item.reasons) for item in blocked.rejected_signals)
    allowed = run([100,100,100],st=st.model_copy(update={"min_liquidation_distance_fraction":D(".1")}))
    assert allowed.trades
