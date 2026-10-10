from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_default=True)


class LoginInput(Input):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=256)
    code: str | None = Field(default=None, pattern=r"^\d{6}$", repr=False)
    recovery_code: str | None = Field(default=None, min_length=16, max_length=64, repr=False)


class StrategyInput(Input):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=5000)
    config: dict[str, Any]


class StrategyUpdate(Input):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=5000)
    config: dict[str, Any] | None = None


class AccountInput(Input):
    initial_balance: Decimal = Field(gt=0, le=Decimal("100000000"))
    settlement_asset: Literal["USD", "USDT", "INR"] = "USD"
    planning_capital_inr: Decimal = Field(default=50000, gt=0)
    usd_inr_rate: Decimal | None = Field(default=None, gt=0)


class RiskInput(Input):
    max_risk_per_trade: Decimal = Field(default=Decimal("0.01"), gt=0, le=Decimal("0.05"))
    max_total_risk: Decimal = Field(default=Decimal("0.03"), gt=0, le=Decimal("0.20"))
    max_daily_loss: Decimal = Field(default=Decimal("0.05"), gt=0, le=Decimal("0.25"))
    max_weekly_loss: Decimal = Field(default=Decimal("0.10"), gt=0, le=Decimal("0.50"))
    max_drawdown: Decimal = Field(default=Decimal("0.15"), gt=0, le=Decimal("0.50"))
    max_leverage: Decimal = Field(default=2, ge=1, le=5)
    max_open_positions: int = Field(default=3, ge=1, le=10)
    max_pending_orders: int = Field(default=10, ge=1, le=100)
    max_margin_fraction: Decimal = Field(default=Decimal("0.5"), gt=0, le=1)
    max_gross_exposure_multiple: Decimal = Field(default=2, gt=0, le=10)
    max_net_exposure_multiple: Decimal = Field(default=2, gt=0, le=10)
    max_correlated_exposure_multiple: Decimal | None = Field(default=None, gt=0, le=10)
    correlation_groups: list[Annotated[list[Annotated[str, Field(pattern=r"^[A-Z0-9]{3,32}$")]], Field(min_length=2, max_length=40)]] = Field(default_factory=list, max_length=20)
    max_spread_bps: Decimal = Field(default=10, ge=0, le=100)
    max_slippage_bps: Decimal = Field(default=25, ge=0, le=100)
    stale_after_seconds: int = Field(default=120, ge=30, le=300)
    entries_enabled: bool = True

    @model_validator(mode="after")
    def explicit_correlation_groups(self):
        if bool(self.correlation_groups) != (self.max_correlated_exposure_multiple is not None):
            raise ValueError("Correlated exposure requires both an explicit cap and symbol groups")
        if any(len(group) != len(set(group)) for group in self.correlation_groups):
            raise ValueError("Correlation groups cannot repeat a symbol")
        if len({tuple(sorted(group)) for group in self.correlation_groups}) != len(self.correlation_groups):
            raise ValueError("Correlation groups cannot repeat the same members")
        return self


class BacktestInput(Input):
    strategy_id: int = Field(gt=0)
    initial_capital: Decimal = Field(default=10000, gt=0)
    limit: int = Field(default=2000, ge=50, le=10000)
    dataset_id: str | None = Field(default=None, min_length=64, max_length=64, pattern=r"^[a-f0-9]{64}$")
    fee_rate: Decimal = Field(default=Decimal("0.0005"), ge=0, lt=1)
    spread_bps: Decimal = Field(default=1, ge=0, le=1000)
    slippage_bps: Decimal = Field(default=2, ge=0, le=1000)
    funding_events: list[dict[str, Any]] = Field(default_factory=list, max_length=2000)


class MarketImportInput(Input):
    symbol: str = Field(default="BTCUSD", pattern=r"^[A-Z0-9]{3,32}$")
    timeframe_seconds: Literal[60, 180, 300, 900, 1800, 3600, 7200, 14400, 21600, 86400] = 60
    lookback_bars: int = Field(default=500, ge=50, le=5000)


class EmergencyInput(Input):
    action: Literal["BLOCK_ENTRIES", "RESUME_ENTRIES", "FLATTEN_MANAGED"] = "BLOCK_ENTRIES"
    confirm: bool = False


class SettingsInput(Input):
    planning_capital_inr: Decimal = Field(gt=0)
    usd_inr_rate: Decimal | None = Field(default=None, gt=0)
