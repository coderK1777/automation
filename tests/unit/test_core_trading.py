"""Offline fixtures test financial math; none claim actual Delta metadata."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext

import pytest
from pydantic import ValidationError

from tradebot_core import (
    BacktestConfig,
    BacktestEngine,
    Candle,
    FundingEvent,
    Instrument,
    Operand,
    OrderState,
    OrderStatus,
    PaperFillModel,
    PositionSize,
    RiskContext,
    RiskLimits,
    RuleNode,
    Side,
    StrategyConfig,
    evaluate_risk,
    evaluate_rule,
    evaluate_strategy,
    size_position,
    transition_order,
    validate_candles,
)

D = Decimal
T = datetime(2026, 1, 1, tzinfo=timezone.utc)


def instrument(**changes):
    data = dict(
        product_id=1,
        symbol="BTCUSD",
        underlying_asset="BTC",
        quote_asset="USD",
        settlement_asset="USD",
        contract_size=D(".001"),
        tick_size=D(".5"),
        quantity_step=D(1),
        min_quantity=D(1),
        initial_margin_rate=D(".1"),
        metadata_verified=True,
        metadata_timestamp=T,
    )
    data.update(changes)
    return Instrument(**data)


def candle(index, price="100", **changes):
    p = D(price)
    data = dict(timestamp=T + timedelta(minutes=index), open=p, high=p, low=p, close=p, volume=D("10"))
    data.update(changes)
    return Candle(**data)


def healthy(**changes):
    data = dict(
        equity=D("50000"),
        available_margin=D("50000"),
        day_start_equity=D("50000"),
        week_start_equity=D("50000"),
        peak_equity=D("50000"),
        now=T,
        market_timestamp=T,
        account_reconciled=True,
        instrument_metadata_fresh=True,
        data_healthy=True,
        exchange_healthy=True,
        worker_healthy=True,
        clock_healthy=True,
        database_healthy=True,
        protective_orders_verified=True,
        has_unknown_orders=False,
    )
    data.update(changes)
    return RiskContext(**data)


def sized(**changes):
    data = dict(
        instrument=instrument(),
        side=Side.BUY,
        entry_price=D("100000"),
        stop_price=D("99000"),
        equity=D("50000"),
        risk_fraction=D(".01"),
        max_leverage=D("2"),
        fee_rate=D(0),
        slippage_bps=D(0),
        spread_bps=D(0),
    )
    data.update(changes)
    return size_position(**data)


def check_risk(context=None, size=None, **changes):
    data = dict(
        limits=RiskLimits(),
        context=context or healthy(),
        instrument=instrument(),
        side=Side.BUY,
        size=size or sized(),
        target_price=D("102000"),
        leverage=D("2"),
        fee_rate=D(0),
        slippage_bps=D(0),
        spread_bps=D(0),
    )
    data.update(changes)
    return evaluate_risk(**data)


def always_strategy(**changes):
    data = dict(
        name="offline always long",
        kind="CUSTOM",
        symbol="BTCUSD",
        stop_loss_pct=D(".1"),
        take_profit_pct=D(".2"),
        long_rules=RuleNode(op="GT", left=Operand(feature="close"), right=Operand(value=D(0))),
    )
    data.update(changes)
    return StrategyConfig(**data)


def run(prices, config=None, strategy=None, ins=None):
    candles = [candle(i, p) if not isinstance(p, dict) else candle(i, **p) for i, p in enumerate(prices)]
    config = config or BacktestConfig(
        initial_capital=D("1000"), dataset_version="offline-test-v1", fee_rate=D(0), spread_bps=D(0), slippage_bps=D(0)
    )
    return BacktestEngine().run(
        candles, ins or instrument(contract_size=D(".1"), tick_size=D(".01")), strategy or always_strategy(), config
    )


def test_exact_contract_sizing_stop_risk_not_profit_goal():
    result = sized()
    assert result.quantity == D(500)
    assert result.risk_amount == D(500)
    assert result.notional == D(50000)
    assert result.margin == D(25000)


def test_fees_slippage_and_tick_costs_conservatively_reduce_quantity():
    result = sized(fee_rate=D(".0005"), slippage_bps=D(2), spread_bps=D(1))
    assert result.quantity < D(500)
    assert result.risk_amount <= D(500)
    assert result.risk_amount / result.quantity * (result.quantity + 1) > D(500)
    assert result.entry_price % D(".5") == 0
    assert result.estimated_fees > 0


def test_quantity_increments_and_minimum_never_round_up():
    result = sized(instrument=instrument(quantity_step=D(10), min_quantity=D(10)), risk_fraction=D(".00001"))
    assert result.quantity == 0
    result = sized(instrument=instrument(quantity_step=D(10), min_quantity=D(10)), fee_rate=D(".0005"))
    assert result.quantity % 10 == 0
    assert result.risk_amount <= 500


def test_margin_and_notional_caps_apply_before_risk_rounding():
    assert sized(available_margin=D(100)).quantity == D(2)
    assert sized(max_notional=D(150)).quantity == D(1)
    assert sized(instrument=instrument(max_quantity=D(20))).quantity == D(20)


def test_margin_uses_stricter_exchange_requirement_than_requested_leverage():
    assert instrument(initial_margin_rate=D(".6")).margin(D(100000), D(1), D(2)) == D(60)


def test_linear_contract_pnl_signed_fees_and_conversion():
    ins = instrument(settlement_asset="INR", quote_to_settlement_rate=D(85), conversion_timestamp=T)
    assert ins.pnl(D(100000), D(101000), D(10), Side.BUY) == D(850)
    assert ins.pnl(D(100000), D(101000), D(10), Side.SELL) == D(-850)
    assert ins.fee(D(100000), D(10), D(".001")) == D(85)


@pytest.mark.parametrize(
    "changes",
    [
        dict(contract_type="INVERSE"),
        dict(contract_size=0),
        dict(tick_size=0),
        dict(settlement_asset="INR"),
        dict(min_quantity=D(".5")),
        dict(quote_to_settlement_rate=D(85)),
        dict(max_quantity=0),
    ],
)
def test_unsupported_or_unverified_economics_do_not_silently_convert(changes):
    with pytest.raises(ValidationError):
        instrument(**changes)


def test_unverified_metadata_blocks_sizing():
    with pytest.raises(ValueError, match="verified"):
        sized(instrument=instrument(metadata_verified=False))


@pytest.mark.parametrize("side,stop", [(Side.BUY, "101000"), (Side.SELL, "99000")])
def test_invalid_stop_direction_rejected(side, stop):
    with pytest.raises(ValueError, match="loss side"):
        sized(side=side, stop_price=D(stop))


def test_candles_reject_missing_duplicate_out_of_order_and_misaligned_bars():
    for candles in ([candle(0), candle(2)], [candle(0), candle(0)], [candle(1), candle(0)]):
        with pytest.raises(ValueError):
            validate_candles(candles)
    with pytest.raises(ValidationError):
        candle(0, timestamp=T + timedelta(seconds=1))
    with pytest.raises(ValidationError):
        candle(0, timestamp=T.replace(tzinfo=None))


def test_nested_rule_tree_and_three_valued_warmup_are_safe():
    gt = RuleNode(op="GT", left=Operand(feature="rsi"), right=Operand(value=D(30)))
    tree = RuleNode(
        op="AND",
        children=[
            gt,
            RuleNode(op="NOT", children=[RuleNode(op="LT", left=Operand(feature="close"), right=Operand(value=D(50)))]),
        ],
    )
    assert evaluate_rule(tree, {"rsi": D(40), "close": D(100)}) is True
    assert evaluate_rule(RuleNode(op="NOT", children=[gt]), {"rsi": None}) is None
    assert evaluate_rule(tree, {"rsi": None, "close": D(100)}) is None
    with pytest.raises(ValidationError):
        Operand(feature="__import__('os')")
    with pytest.raises(ValidationError):
        RuleNode(op="NOT", children=[])
    with pytest.raises(ValidationError):
        StrategyConfig(name="custom", kind="CUSTOM")


def test_rule_cross_requires_previous_warmed_value():
    node = RuleNode(op="CROSS_ABOVE", left=Operand(feature="ema_fast"), right=Operand(feature="ema_slow"))
    assert evaluate_rule(node, {"ema_fast": D(2), "ema_slow": D(1)}, {"ema_fast": D(1), "ema_slow": D(1)}) is True
    assert evaluate_rule(node, {"ema_fast": D(2), "ema_slow": D(1)}, {"ema_fast": None, "ema_slow": D(1)}) is None


def test_breakout_excludes_current_bar_high_and_open_candle():
    config = StrategyConfig(name="breakout", kind="BREAKOUT", lookback=2)
    candles = [candle(0), candle(1), candle(2, "110", high=D(115), low=D(100))]
    signal = evaluate_strategy(config, candles)
    assert signal.side is Side.BUY
    assert signal.timestamp == T + timedelta(minutes=3)
    assert evaluate_strategy(config, candles + [candle(3, "10", closed=False)]).signal_id == signal.signal_id
    assert evaluate_strategy(config, candles[:2]) is None


def test_ema_warmup_and_no_lookahead_signal_is_deterministic():
    cfg = StrategyConfig(name="ema", fast_period=2, slow_period=3)
    bars = [candle(i, p) for i, p in enumerate(["100", "90", "80", "90", "100"])]
    assert evaluate_strategy(cfg, bars[:3]) is None
    signal = evaluate_strategy(cfg, bars[:5])
    assert signal.side is Side.BUY
    assert signal == evaluate_strategy(cfg, bars[:5])
    assert signal == evaluate_strategy(cfg, bars + [candle(5, "1", closed=False)])


def test_conflicting_custom_long_short_blocks_exposure():
    node = RuleNode(op="GT", left=Operand(feature="close"), right=Operand(value=D(0)))
    assert evaluate_strategy(always_strategy(short_rules=node), [candle(0)]) is None


def test_default_risk_context_fails_closed():
    ctx = RiskContext(
        equity=D(50000),
        available_margin=D(50000),
        day_start_equity=D(50000),
        week_start_equity=D(50000),
        peak_equity=D(50000),
        now=T,
        market_timestamp=T,
    )
    decision = check_risk(context=ctx)
    assert not decision.approved
    assert {"UNKNOWN_ORDER", "UNRECONCILED_ACCOUNT", "PROTECTION_UNVERIFIED"} <= set(decision.reasons)
    assert check_risk().approved


@pytest.mark.parametrize(
    "changes,reason",
    [
        ({"has_unknown_orders": True}, "UNKNOWN_ORDER"),
        ({"account_reconciled": False}, "UNRECONCILED_ACCOUNT"),
        ({"now": T + timedelta(minutes=10)}, "STALE_OR_FUTURE_MARKET_DATA"),
        ({"daily_pnl": D(-2500)}, "DAILY_LOSS_LIMIT"),
        ({"weekly_pnl": D(-5000)}, "WEEKLY_LOSS_LIMIT"),
        ({"gross_exposure": D(60000)}, "GROSS_EXPOSURE_LIMIT"),
        ({"margin_used": D(1)}, "MARGIN_LIMIT"),
        ({"open_positions": 3}, "POSITION_LIMIT"),
        ({"pending_orders": 10}, "PENDING_ORDER_LIMIT"),
        ({"equity": D(45000)}, "DRAWDOWN_LIMIT"),
        ({"reserved_risk": D(2500)}, "COMMITTED_RISK_LIMIT"),
        ({"emergency_stop": True}, "EMERGENCY_STOP"),
    ],
)
def test_portfolio_and_operational_risk_gates(changes, reason):
    decision = check_risk(context=healthy(**changes))
    assert not decision.approved
    assert reason in decision.reasons


def test_forged_sizing_numbers_cannot_bypass_central_risk():
    real = sized()
    data = real.model_dump()
    data.update(risk_amount=D(1), margin=D(1), notional=D(1))
    assert "INCONSISTENT_SIZE_ECONOMICS" in check_risk(size=PositionSize(**data)).reasons


def test_paper_and_backtest_share_adverse_execution_math():
    model = PaperFillModel(fee_rate=D(".001"), spread_bps=D(10), slippage_bps=D(5))
    buy = model.fill(instrument(), Side.BUY, D(10), D(100000))
    sell = model.fill(instrument(), Side.SELL, D(10), D(100000))
    assert buy.price == D(100100)
    assert sell.price == D(99900)
    assert buy.fee == D("1.001")


def test_unknown_order_cannot_retry_or_resolve_without_evidence():
    with pytest.raises(ValueError):
        transition_order(OrderStatus.UNKNOWN, OrderStatus.SUBMITTING)
    with pytest.raises(ValueError, match="reconciliation"):
        transition_order(OrderStatus.UNKNOWN, OrderStatus.ACKNOWLEDGED)
    assert transition_order(OrderStatus.UNKNOWN, OrderStatus.ACKNOWLEDGED, reconciled=True) == OrderStatus.ACKNOWLEDGED
    with pytest.raises(ValueError):
        transition_order(OrderStatus.FILLED, OrderStatus.SUBMITTING)


def test_order_fill_monotonicity_and_partial_fill_after_cancel():
    state = OrderState(order_id="one", client_order_id="one", quantity=D(10))
    state = state.transition(OrderStatus.RISK_APPROVED).transition(OrderStatus.SUBMITTING)
    state = state.transition(OrderStatus.ACKNOWLEDGED).transition(OrderStatus.CANCEL_PENDING)
    state = state.transition(OrderStatus.PARTIALLY_FILLED, filled_quantity=D(2))
    with pytest.raises(ValueError):
        state.transition(OrderStatus.PARTIALLY_FILLED, filled_quantity=D(1))
    with pytest.raises(ValidationError):
        state.transition(OrderStatus.FILLED, filled_quantity=D(9))
    assert state.transition(OrderStatus.FILLED, filled_quantity=D(10)).filled_quantity == D(10)


def test_backtest_signal_executed_at_next_open_never_same_close():
    result = run(["100", dict(price="105", open=D(105), high=D(105), low=D(100), close=D(104)), "106"])
    trade = result.trades[0]
    assert trade.signal_timestamp == T + timedelta(minutes=1)
    assert trade.entry_timestamp == T + timedelta(minutes=1)
    assert trade.entry_price == D(105)
    assert trade.exit_reason == "END_OF_DATA"
    assert result.metrics.sharpe_ratio is None
    assert result.metrics.daily_return_samples == 0


def test_backtest_stop_first_when_stop_and_target_in_one_bar():
    result = run(["100", dict(price="100", high=D(125), low=D(85)), "100"])
    assert result.trades[0].exit_price == D(90)
    assert result.trades[0].exit_reason == "STOP_FIRST_AMBIGUOUS"
    assert result.trades[0].net_pnl < 0


def test_stop_gap_uses_worse_open_not_unavailable_stop_price():
    result = run(["100", "100", dict(price="80", high=D(85), low=D(75))])
    assert result.trades[0].exit_price == D(80)
    assert result.trades[0].exit_reason == "STOP_GAP"
    assert result.metrics.net_profit == sum((trade.net_pnl for trade in result.trades), D(0))


def test_short_stop_and_funding_signs():
    rule = RuleNode(op="GT", left=Operand(feature="close"), right=Operand(value=D(0)))
    strategy = always_strategy(long_rules=None, short_rules=rule)
    cfg = BacktestConfig(
        initial_capital=D(1000),
        dataset_version="fixture",
        fee_rate=D(0),
        spread_bps=D(0),
        slippage_bps=D(0),
        funding_mode="PROVIDED",
        funding_events=[
            FundingEvent(timestamp=T + timedelta(minutes=2), rate=D(".01"), mark_price=D(100), source="offline")
        ],
    )
    result = run(["100", "100", "100"], cfg, strategy)
    assert result.trades[0].side is Side.SELL
    assert result.trades[0].funding == D(-1)
    assert result.trades[0].net_pnl == D(1)
    assert result.ending_equity == D(1001)


def test_long_funding_applies_only_if_position_preexists_event():
    cfg = BacktestConfig(
        initial_capital=D(1000),
        dataset_version="fixture",
        fee_rate=D(0),
        spread_bps=D(0),
        slippage_bps=D(0),
        funding_mode="PROVIDED",
        funding_events=[
            FundingEvent(timestamp=T + timedelta(minutes=1), rate=D(".01"), mark_price=D(100), source="offline"),
            FundingEvent(timestamp=T + timedelta(minutes=2), rate=D(".01"), mark_price=D(100), source="offline"),
        ],
    )
    result = run(["100", "100", "100"], cfg)
    assert result.trades[0].funding == D(1)
    assert result.trades[0].net_pnl == D(-1)


def test_reproducibility_hash_and_results_include_dataset_economics_costs():
    one = run(["100", "100", "110"])
    two = run(["100", "100", "110"])
    assert one == two
    config = BacktestConfig(
        initial_capital=D(1000), dataset_version="revision-two", fee_rate=D(0), spread_bps=D(0), slippage_bps=D(0)
    )
    revised = run(["100", "100", "110"], config)
    assert revised.reproducibility_hash != one.reproducibility_hash
    assert revised.dataset_hash == one.dataset_hash
    assert run(["100", "100", "109"]).dataset_hash != one.dataset_hash
    with localcontext() as ctx:
        ctx.prec = 7
        assert run(["100", "100", "110"]) == one
    assert one.model_dump(mode="json")["environment"] == "BACKTEST"


def test_backtest_fee_accounting_matches_net_cash():
    cfg = BacktestConfig(
        initial_capital=D(1000), dataset_version="fixture", fee_rate=D(".001"), spread_bps=D(1), slippage_bps=D(2)
    )
    result = run(["100", "100", "110"], cfg)
    assert result.metrics.fees > 0
    assert result.ending_equity - result.starting_capital == sum((t.net_pnl for t in result.trades), D(0))
    assert result.metrics.profit_factor is None  # no loss denominator, no fake infinity


def test_missing_candles_and_unverified_metadata_block_backtest():
    with pytest.raises(ValueError):
        BacktestEngine().run(
            [candle(0), candle(2)], instrument(), always_strategy(), BacktestConfig(dataset_version="x")
        )
    with pytest.raises(ValueError, match="reviewed"):
        run(["100", "100"], ins=instrument(metadata_verified=False))
    with pytest.raises(ValidationError):
        BacktestConfig()


def test_all_financial_math_ignores_external_decimal_precision_and_rounding():
    from decimal import ROUND_UP

    expected = sized(fee_rate=D(".0005"), slippage_bps=D(2), spread_bps=D(1))
    expected_decision = check_risk(size=expected, fee_rate=D(".0005"), slippage_bps=D(2), spread_bps=D(1))
    with localcontext() as context:
        context.prec = 7
        context.rounding = ROUND_UP
        actual = sized(fee_rate=D(".0005"), slippage_bps=D(2), spread_bps=D(1))
        assert actual == expected
        assert check_risk(size=actual, fee_rate=D(".0005"), slippage_bps=D(2), spread_bps=D(1)) == expected_decision


def test_stable_signal_identity_survives_history_revision_without_duplicate_intent():
    config = always_strategy()
    before = evaluate_strategy(config, [candle(0), candle(1)])
    after = evaluate_strategy(config, [candle(0, "99"), candle(1)])
    assert before.signal_id == after.signal_id
    assert before.data_snapshot_id != after.data_snapshot_id


def daily_curve(count, *, constant=False, gap=False, partial=False):
    from tradebot_core.backtest import EquityPoint

    equity = D(1000)
    points = []
    for index in range(count):
        if not constant:
            equity *= D("1.01") if index % 2 else D(".99")
        stamp = T + timedelta(days=index * (2 if gap else 1))
        if partial:
            stamp += timedelta(hours=1)
        points.append(
            EquityPoint(
                timestamp=stamp, equity=equity, cash=equity, unrealized_pnl=D(0), gross_exposure=D(0), margin_used=D(0)
            )
        )
    return points


def test_sharpe_sortino_need_thirty_complete_daily_intervals_and_variance():
    from tradebot_core import calculate_metrics

    curve = daily_curve(31)
    metrics = calculate_metrics(D(1000), curve[-1].equity, [], curve)
    assert metrics.daily_return_samples == 30
    assert metrics.sharpe_ratio is not None
    assert metrics.sortino_ratio is not None
    assert metrics.calmar_ratio is None
    small = calculate_metrics(D(1000), curve[-2].equity, [], curve[:-1])
    assert small.daily_return_samples == 29
    assert small.sharpe_ratio is None
    assert small.sortino_ratio is None
    constant = daily_curve(40, constant=True)
    assert calculate_metrics(D(1000), D(1000), [], constant).sharpe_ratio is None


@pytest.mark.parametrize("options", [{"gap": True}, {"partial": True}])
def test_partial_or_nonadjacent_observations_are_not_fake_daily_return_samples(options):
    from tradebot_core import calculate_metrics

    curve = daily_curve(40, **options)
    metrics = calculate_metrics(D(1000), curve[-1].equity, [], curve)
    assert metrics.daily_return_samples == 0
    assert metrics.sharpe_ratio is None


def test_funding_events_need_explicit_model_and_alignment():
    event = FundingEvent(timestamp=T + timedelta(minutes=1), rate=D(".001"), mark_price=D(100), source="offline")
    with pytest.raises(ValidationError):
        BacktestConfig(dataset_version="x", funding_events=[event])
    with pytest.raises(ValidationError):
        BacktestConfig(dataset_version="x", funding_mode="PROVIDED", funding_events=[event, event])
    config = BacktestConfig(
        dataset_version="x",
        funding_mode="PROVIDED",
        funding_events=[event.model_copy(update={"timestamp": T + timedelta(seconds=1)})],
    )
    with pytest.raises(ValueError, match="align"):
        run(["100", "100", "100"], config)


@pytest.mark.parametrize("kind", ["EMA_CROSS", "BREAKOUT", "MEAN_REVERSION", "CUSTOM"])
def test_prepared_strategy_cache_is_identical_to_prefix_only_evaluation(kind):
    from tradebot_core import StrategyEvaluator

    config = always_strategy(kind=kind, fast_period=2, slow_period=3, lookback=3, rsi_period=2)
    prices = ["100", "99", "98", "99", "103", "98", "94", "99", "110", "95", "101", "99"]
    bars = [candle(index, price) for index, price in enumerate(prices)]
    prepared = StrategyEvaluator(config, bars)
    revised = StrategyEvaluator(config, bars[:-1] + [candle(len(bars) - 1, "5000")])
    for index in range(len(bars)):
        assert prepared.signal_at(index) == evaluate_strategy(config, bars[: index + 1])
        if index < len(bars) - 1:
            assert prepared.signal_at(index) == revised.signal_at(index)


@pytest.mark.parametrize("environment", ["DEMO", "LIVE"])
def test_internal_paper_economics_can_never_authorize_exchange_execution(environment):
    ins = instrument(verification_scope="PAPER_MODEL", assumptions=["Offline lot minimum and margin model"])
    decision = check_risk(context=healthy(execution_environment=environment), instrument=ins)
    assert not decision.approved
    assert "PAPER_MODEL_NOT_EXCHANGE_VERIFIED" in decision.reasons


def test_paper_model_declares_and_serializes_assumptions_in_reproducibility():
    with pytest.raises(ValidationError, match="declared assumptions"):
        instrument(verification_scope="PAPER_MODEL")
    ins = instrument(
        contract_size=D(".1"),
        tick_size=D(".01"),
        verification_scope="PAPER_MODEL",
        assumptions=["Offline quantity and conservative margin assumptions"],
    )
    result = run(["100", "100", "110"], ins=ins)
    assert result.instrument.verification_scope == "PAPER_MODEL"
    assert ins.assumptions[0] in result.assumptions
    assert any("PAPER assumptions" in warning for warning in result.warnings)


def test_fixed_cross_currency_backtest_is_explicit_while_runtime_requires_fresh_rate():
    ins = instrument(
        contract_size=D(".001"),
        tick_size=D(".01"),
        settlement_asset="INR",
        quote_to_settlement_rate=D(85),
        conversion_timestamp=T + timedelta(days=100),
        verification_scope="PAPER_MODEL",
        assumptions=["Offline fixed FX research assumption"],
    )
    result = run(["100", "100", "110"], ins=ins)
    assert result.currency == "INR"
    assert result.trades
    size = sized(instrument=ins)
    runtime = check_risk(instrument=ins, size=size)
    assert "STALE_OR_FUTURE_CONVERSION" in runtime.reasons
