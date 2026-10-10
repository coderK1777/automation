"""Per-strategy PAPER gates from attributed ledger costs and observed equity.

Every baseline is an observation for an immutable strategy version. It is not a
reconstruction of an unseen historical peak or an exchange margin certificate.
"""
from decimal import Decimal

from sqlalchemy import select

from tradebot.db import aware, utcnow
from tradebot.expansion_models import PaperMovement, PaperPositionState
from tradebot.models import Fill, Order, Position, Signal, StrategyVersion
from tradebot.repository import open_positions, unrealized
from tradebot.strategy_risk_models import StrategyRiskState

D = Decimal
ZERO = D(0)
TOLERANCE = D("0.0000000001")


class InvalidStrategyLedger(ValueError):
    pass


def _positions(db, strategy_id, version):
    """Old positions can prove their version from the unique entry signal."""
    result = []
    for position in db.scalars(select(Position).where(Position.strategy_id == strategy_id)):
        state = db.get(PaperPositionState, position.id)
        orders = list(db.scalars(select(Order).where(Order.position_id == position.id, Order.reduce_only.is_(False))))
        signal = db.get(Signal, orders[0].signal_id) if len(orders) == 1 and orders[0].signal_id else None
        observed_version = state.strategy_version if state else signal.version if signal else None
        if observed_version is None:
            raise InvalidStrategyLedger("Position version lacks durable evidence")
        if signal and state and signal.version != state.strategy_version:
            raise InvalidStrategyLedger("Position and signal version evidence disagree")
        if observed_version == version:
            result.append(position)
    return result


def _position_pnl(db, position, account):
    from tradebot_core import Instrument, Side
    rows = list(db.execute(select(Fill, Order).join(Order).where(Order.position_id == position.id)))
    entries = [(fill, order) for fill, order in rows if not order.reduce_only]
    exits = [(fill, order) for fill, order in rows if order.reduce_only]
    if not entries or len({order.id for _, order in entries}) != 1:
        raise InvalidStrategyLedger("Position lacks a unique durable entry")
    economics = Instrument.model_validate(position.instrument_snapshot)
    if economics.settlement_asset != account.settlement_asset or economics.symbol != position.symbol:
        raise InvalidStrategyLedger("Strategy ledger currency differs from the account")
    if any(fill.quantity <= 0 or fill.fee < 0 or fill.position_id != position.id or order.account_id != account.id
           or order.mode != "PAPER" or order.strategy_id != position.strategy_id or order.symbol != position.symbol for fill, order in rows):
        raise InvalidStrategyLedger("Fill ownership, quantity or costs are invalid")
    if any(order.side != position.side or fill.realized_pnl != 0 for fill, order in entries):
        raise InvalidStrategyLedger("Entry side or realized amount is invalid")
    quantity = sum((fill.quantity for fill, _ in entries), ZERO)
    exit_quantity = sum((fill.quantity for fill, _ in exits), ZERO)
    expected = position.quantity if position.status == "OPEN" else ZERO
    if abs(quantity-exit_quantity-expected) > TOLERANCE:
        raise InvalidStrategyLedger("Position quantity does not reconcile to entry and exit fills")
    average = sum((fill.price*fill.quantity for fill, _ in entries), ZERO)/quantity
    if abs(average-position.entry_price) > TOLERANCE:
        raise InvalidStrategyLedger("Position average differs from entry fills")
    side = Side(position.side)
    for fill, order in exits:
        if order.side != side.opposite.value or abs(fill.realized_pnl-economics.pnl(average, fill.price, fill.quantity, side)) > TOLERANCE:
            raise InvalidStrategyLedger("Exit P&L does not match captured economics")
    pnl = sum((fill.realized_pnl-fill.fee for fill, _ in rows), ZERO)
    pnl += sum((movement.amount for movement in db.scalars(select(PaperMovement).where(
            PaperMovement.position_id == position.id, PaperMovement.account_id == account.id, PaperMovement.kind != "RESET"))), ZERO)
    return pnl+unrealized(position) if position.status == "OPEN" else pnl


def _version_pnl(db, strategy_id, version, account):
    positions = _positions(db, strategy_id, version)
    pnl = sum((_position_pnl(db, position, account) for position in positions), ZERO)
    return pnl, positions


def _observe(db, state, account):
    pnl, _ = _version_pnl(db, state.strategy_id, state.version, account)
    state.current_equity = state.baseline_equity+pnl-state.baseline_pnl
    state.peak_equity = max(state.peak_equity, state.current_equity)
    state.ledger_valid = True
    state.updated_at = utcnow()


def snapshot_strategy_equities(db, account):
    """Call on every persisted mark/fill/financing snapshot under account lock."""
    for state in db.scalars(select(StrategyRiskState)):
        try:
            _observe(db, state, account)
        except (ValueError, ArithmeticError):
            state.ledger_valid = False
            state.updated_at = utcnow()


def strategy_risk_reasons(db, strategy_id, config, account, *, equity=None, own_position=None):
    reasons = []
    active = [position for position in open_positions(db) if position.strategy_id == strategy_id and position is not own_position]
    if config.max_open_positions is not None and len(active) >= config.max_open_positions:
        reasons.append("STRATEGY_POSITION_LIMIT")
    needed = config.max_consecutive_losses is not None or config.max_drawdown_fraction is not None
    state = db.get(StrategyRiskState, (strategy_id, config.version))
    if not needed and state is None:
        return reasons
    try:
        pnl, positions = _version_pnl(db, strategy_id, config.version, account)
        if config.max_consecutive_losses is not None:
            closed = sorted((position for position in positions if position.status == "CLOSED"),
                            key=lambda position: (aware(position.closed_at or position.opened_at), position.id), reverse=True)
            losses = 0
            for position in closed:
                if _position_pnl(db, position, account) >= -TOLERANCE:
                    break
                losses += 1
            if losses >= config.max_consecutive_losses:
                reasons.append("STRATEGY_CONSECUTIVE_LOSS_LIMIT")
        if state is None and config.max_drawdown_fraction is not None:
            if positions:
                return reasons+["STRATEGY_RISK_BASELINE_MISSING"]
            observed_equity = equity if equity is not None else account.cash+sum((unrealized(position) for position in open_positions(db)), ZERO)
            baseline = observed_equity*(config.capital_allocation_fraction or D(1))
            if baseline <= 0:
                return reasons+["STRATEGY_RISK_BASELINE_INVALID"]
            state = StrategyRiskState(strategy_id=strategy_id, version=config.version, baseline_equity=baseline,
                    baseline_pnl=pnl, current_equity=baseline, peak_equity=baseline, ledger_valid=True,
                    note="Observed before first entry for this immutable version; no earlier historical peak is inferred.")
            db.add(state)
            db.flush()
        if state:
            _observe(db, state, account)
            if config.max_drawdown_fraction is not None and (state.peak_equity <= 0 or
                    state.current_equity <= state.peak_equity*(1-config.max_drawdown_fraction)):
                reasons.append("STRATEGY_DRAWDOWN_LIMIT")
    except (ValueError, ArithmeticError):
        if state:
            state.ledger_valid = False
        reasons.append("STRATEGY_LEDGER_INVALID")
    return reasons


def strategy_liquidation_reasons(db, config, *, instrument, side, size, own_position=None):
    if config.min_liquidation_distance_fraction is None:
        return []
    if not config.execution or config.execution.maintenance_margin_rate is None:
        return ["STRATEGY_LIQUIDATION_MODEL_MISSING"]
    from tradebot_core.simulation import isolated_liquidation_price
    try:
        quantity, entry, margin, funding = size.quantity, size.entry_price, size.margin, ZERO
        if own_position:
            state = db.get(PaperPositionState, own_position.id)
            if not state:
                return ["STRATEGY_LIQUIDATION_HISTORY_MISSING"]
            quantity += own_position.quantity
            entry = (own_position.entry_price*own_position.quantity+size.entry_price*size.quantity)/quantity
            margin += own_position.margin
            stored = state.entry_observations.get("isolated_funding_paid")
            funding = D(stored) if stored is not None else state.funding_paid*own_position.quantity/state.entry_quantity
        liquidation = isolated_liquidation_price(instrument=instrument, side=side, entry_price=entry,
                         quantity=quantity, isolated_margin=margin, funding_paid=funding,
                         maintenance_margin_rate=config.execution.maintenance_margin_rate,
                         liquidation_fee_rate=config.execution.liquidation_fee_rate)
        if liquidation is None:
            return []  # No positive price threshold in this declared isolated model.
        distance = (entry-liquidation)*side.sign/entry
        return ["STRATEGY_LIQUIDATION_DISTANCE"] if distance < config.min_liquidation_distance_fraction else []
    except (ValueError, ArithmeticError):
        return ["STRATEGY_LIQUIDATION_MODEL_INVALID"]


def strategy_risk_state_output(db, account):
    """Read-only public evidence; no invented baseline for old positions."""
    result = []
    for state in db.scalars(select(StrategyRiskState).order_by(StrategyRiskState.strategy_id, StrategyRiskState.version)):
        saved = db.scalar(select(StrategyVersion).where(StrategyVersion.strategy_id == state.strategy_id, StrategyVersion.version == state.version))
        result.append({"strategy_id": state.strategy_id, "version": state.version,
                "baseline_equity": str(state.baseline_equity), "current_equity": str(state.current_equity),
                "peak_equity": str(state.peak_equity), "ledger_valid": state.ledger_valid,
                "observed_at": aware(state.observed_at).isoformat(), "updated_at": aware(state.updated_at).isoformat(),
                "scope": "PAPER_STRATEGY_VERSION", "note": state.note,
                "configured_drawdown_fraction": saved.config.get("max_drawdown_fraction") if saved else None})
    return result
