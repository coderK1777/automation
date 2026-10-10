"use client";
import { asArray, asRecord } from "@/lib/api";
import { Button, Field, Input, Select } from "./ui";
type Row = Record<string, unknown>;
export const strategyOptionKeys = [
  "creator",
  "source",
  "trading_style",
  "allowed_environments",
  "higher_timeframes",
  "allowed_regimes",
  "allowed_volatility",
  "session",
  "filters",
  "cooldown_bars",
  "stop_method",
  "atr_stop_multiple",
  "stop_reference_price",
  "reward_risk_target",
  "exit_rules",
  "execution",
  "sizing",
  "max_consecutive_losses",
  "max_drawdown_fraction",
  "max_open_positions",
  "min_liquidation_distance_fraction",
];
export default function StrategyOptions({
  value,
  onChange,
  baseTimeframe,
}: {
  value: Row;
  onChange: (value: Row) => void;
  baseTimeframe: number;
}) {
  const set = (name: string, next: unknown) =>
    onChange({ ...value, [name]: next });
  const exits = asRecord(value.exit_rules),
    execution = asRecord(value.execution),
    filters = asRecord(value.filters);
  const setExit = (name: string, next: unknown) =>
    set("exit_rules", { ...exits, [name]: next });
  const setExecution = (name: string, next: unknown) =>
    set("execution", { ...execution, [name]: next });
  const setFilter = (name: string, next: unknown) =>
    set("filters", { ...filters, [name]: next });
  const higher = asArray<Row>(value.higher_timeframes);
  const sizing = asRecord(value.sizing);
  const setSizing = (name: string, next: unknown) =>
    set("sizing", { ...sizing, [name]: next });
  const environments = asArray<string>(
    value.allowed_environments ?? ["BACKTEST", "PAPER"],
  );
  const session = asRecord(value.session);
  const targets = asArray<Row>(exits.targets);
  const regimes = asArray<string>(value.allowed_regimes),
    volatility = asArray<string>(value.allowed_volatility);
  return (
    <>
      <div className="form-section">Permitted validation environments</div>
      <div className="button-group">
        {["BACKTEST", "PAPER", "DEMO"].map((environment) => (
          <label className="toggle-field" key={environment}>
            <input
              type="checkbox"
              checked={environments.includes(environment)}
              onChange={(e) =>
                set(
                  "allowed_environments",
                  e.target.checked
                    ? [...environments, environment]
                    : environments.filter((v) => v !== environment),
                )
              }
            />
            {environment}
          </label>
        ))}
      </div>
      <p className="list-note">
        Permitting DEMO in the strategy definition does not connect or authorize
        an exchange account. Backend approval, MFA, independent limits and entry
        authorization remain required.
      </p>
      <div className="form-section">Position sizing policy</div>
      <div className="form-grid">
        <Field label="Position sizing method">
          <Select
            value={String(sizing.mode ?? "RISK_FRACTION")}
            onChange={(event) =>
              set("sizing", {
                ...sizing,
                mode: event.target.value,
                fixed_quantity: null,
                fixed_notional: null,
              })
            }
          >
            <option value="RISK_FRACTION">
              Cost-inclusive stop risk fraction
            </option>
            <option value="FIXED_CONTRACTS">
              Fixed contract quantity, subject to risk caps
            </option>
            <option value="FIXED_NOTIONAL">
              Fixed notional, subject to risk caps
            </option>
            <option value="VOLATILITY">
              Closed-bar volatility-adjusted sizing
            </option>
          </Select>
        </Field>
        {sizing.mode === "FIXED_CONTRACTS" && (
          <Field label="Fixed contract quantity">
            <Input
              required
              type="number"
              min="0.000001"
              step="any"
              value={String(sizing.fixed_quantity ?? "")}
              onChange={(event) =>
                setSizing("fixed_quantity", event.target.value)
              }
            />
          </Field>
        )}
        {sizing.mode === "FIXED_NOTIONAL" && (
          <Field label="Fixed notional in contract settlement currency">
            <Input
              required
              type="number"
              min="0.000001"
              step="any"
              value={String(sizing.fixed_notional ?? "")}
              onChange={(event) =>
                setSizing("fixed_notional", event.target.value)
              }
            />
          </Field>
        )}
        {sizing.mode === "VOLATILITY" && (
          <>
            <Field label="Volatility ATR period">
              <Input
                required
                type="number"
                min="2"
                max="1000"
                step="1"
                value={String(sizing.volatility_period ?? 14)}
                onChange={(event) =>
                  setSizing("volatility_period", Number(event.target.value))
                }
              />
            </Field>
            <Field label="Volatility ATR multiplier">
              <Input
                required
                type="number"
                min="0.1"
                max="100"
                step="0.1"
                value={String(sizing.volatility_multiplier ?? 2)}
                onChange={(event) =>
                  setSizing("volatility_multiplier", event.target.value)
                }
              />
            </Field>
          </>
        )}
        <Field label="Strategy consecutive-loss limit">
          <Input
            type="number"
            min="1"
            step="1"
            value={String(value.max_consecutive_losses ?? "")}
            placeholder="Use portfolio limits only"
            onChange={(event) =>
              set(
                "max_consecutive_losses",
                event.target.value ? Number(event.target.value) : null,
              )
            }
          />
        </Field>
        <Field label="Strategy drawdown limit (fraction)">
          <Input
            type="number"
            min="0.000001"
            max="1"
            step="any"
            value={String(value.max_drawdown_fraction ?? "")}
            placeholder="Use portfolio limits only"
            onChange={(event) =>
              set("max_drawdown_fraction", event.target.value || null)
            }
          />
        </Field>
        <Field label="Strategy open-position limit">
          <Input
            type="number"
            min="1"
            step="1"
            value={String(value.max_open_positions ?? "")}
            placeholder="Use portfolio limits only"
            onChange={(event) =>
              set(
                "max_open_positions",
                event.target.value ? Number(event.target.value) : null,
              )
            }
          />
        </Field>
      </div>
      <p className="list-note">
        Every sizing method remains capped by stop risk, available margin,
        exposure limits and contract increments. Volatility sizing requires
        warmed-up closed-bar ATR; it cannot invent missing observations.
      </p>
      <div className="form-section">Research context & data dependencies</div>
      <div className="form-grid">
        <Field label="Trading style">
          <Select
            value={String(value.trading_style ?? "INTRADAY")}
            onChange={(e) => set("trading_style", e.target.value)}
          >
            {[
              "SCALPING",
              "INTRADAY",
              "SWING",
              "TREND_FOLLOWING",
              "BREAKOUT",
              "MOMENTUM",
              "MEAN_REVERSION",
              "VOLATILITY",
              "SUPPORT_RESISTANCE",
              "MULTI_TIMEFRAME",
              "EVENT_FILTERED",
              "CUSTOM",
            ].map((v) => (
              <option key={v}>{v}</option>
            ))}
          </Select>
        </Field>
        <Field label="Source reference">
          <Input
            value={String(value.source ?? "operator-defined")}
            onChange={(e) => set("source", e.target.value)}
          />
        </Field>
        <Field label="Cooldown after entry (bars)">
          <Input
            type="number"
            min="0"
            max="10000"
            value={String(value.cooldown_bars ?? 0)}
            onChange={(e) => set("cooldown_bars", Number(e.target.value))}
          />
        </Field>
        <Field label="Higher timeframe confirmation">
          <Select
            value={String(higher[0]?.timeframe_seconds ?? "")}
            onChange={(e) =>
              set(
                "higher_timeframes",
                e.target.value
                  ? [
                      {
                        timeframe_seconds: Number(e.target.value),
                        fast_period: 9,
                        slow_period: 21,
                        alignment: "SAME_DIRECTION",
                      },
                    ]
                  : [],
              )
            }
          >
            <option value="">No higher timeframe dependency</option>
            {[300, 900, 3600, 14400, 86400]
              .filter((v) => v > baseTimeframe && v % baseTimeframe === 0)
              .map((v) => (
                <option key={v} value={v}>
                  {v / 60} minutes · same-direction EMA
                </option>
              ))}
          </Select>
        </Field>
      </div>
      <p className="list-note">
        Higher timeframe confirmation uses only bars that had closed at the
        decision time. Additional dependencies and custom regime thresholds are
        available in validated JSON.
      </p>
      <div className="form-grid">
        <Field label="Allowed market regime">
          <Select
            value={regimes[0] ?? ""}
            onChange={(e) =>
              set("allowed_regimes", e.target.value ? [e.target.value] : [])
            }
          >
            <option value="">All regimes</option>
            {["TREND_UP", "TREND_DOWN", "RANGE", "BREAKOUT", "ILLIQUID"].map(
              (v) => (
                <option key={v}>{v}</option>
              ),
            )}
          </Select>
        </Field>
        <Field label="Allowed volatility">
          <Select
            value={volatility[0] ?? ""}
            onChange={(e) =>
              set("allowed_volatility", e.target.value ? [e.target.value] : [])
            }
          >
            <option value="">All volatility bands</option>
            <option>LOW</option>
            <option>NORMAL</option>
            <option>HIGH</option>
          </Select>
        </Field>
        <Field label="Maximum absolute funding rate (fraction)">
          <Input
            type="number"
            step="any"
            min="0"
            value={String(filters.maximum_absolute_funding_rate ?? "")}
            placeholder="No funding filter"
            onChange={(e) =>
              setFilter("maximum_absolute_funding_rate", e.target.value || null)
            }
          />
        </Field>
        <Field label="Stop calculation">
          <Select
            value={String(value.stop_method ?? "FIXED_PCT")}
            onChange={(e) => set("stop_method", e.target.value)}
          >
            <option value="FIXED_PCT">Fixed percentage</option>
            <option value="ATR">ATR multiple</option>
            <option value="STRUCTURE">Prior market structure</option>
            <option value="FIXED_PRICE">Explicit price</option>
          </Select>
        </Field>
        {value.stop_method === "ATR" && (
          <Field label="ATR stop multiple">
            <Input
              type="number"
              min="0.1"
              max="100"
              step="0.1"
              value={String(value.atr_stop_multiple ?? 2)}
              onChange={(e) => set("atr_stop_multiple", e.target.value)}
            />
          </Field>
        )}
        {value.stop_method === "FIXED_PRICE" && (
          <Field label="Explicit stop price">
            <Input
              type="number"
              step="any"
              min="0.000001"
              value={String(value.stop_reference_price ?? "")}
              onChange={(e) =>
                set("stop_reference_price", e.target.value || null)
              }
            />
          </Field>
        )}
        <Field label="Reward / risk target">
          <Input
            type="number"
            min="0.1"
            max="100"
            step="0.1"
            placeholder="Use configured take profit %"
            value={String(value.reward_risk_target ?? "")}
            onChange={(e) => set("reward_risk_target", e.target.value || null)}
          />
        </Field>
      </div>
      <label className="toggle-field">
        <input
          type="checkbox"
          checked={filters.event_filter_enabled === true}
          onChange={(e) => setFilter("event_filter_enabled", e.target.checked)}
        />
        Apply attributed event filters
      </label>
      <label className="toggle-field">
        <input
          type="checkbox"
          checked={value.session != null}
          onChange={(e) =>
            set(
              "session",
              e.target.checked
                ? {
                    start_hour_utc: 0,
                    end_hour_utc: 0,
                    weekdays: [0, 1, 2, 3, 4, 5, 6],
                  }
                : null,
            )
          }
        />
        Limit entries to a UTC trading session
      </label>
      {value.session != null && (
        <div className="form-grid">
          <Field label="UTC start hour">
            <Input
              type="number"
              min="0"
              max="23"
              value={String(session.start_hour_utc ?? 0)}
              onChange={(e) =>
                set("session", {
                  ...session,
                  start_hour_utc: Number(e.target.value),
                })
              }
            />
          </Field>
          <Field label="UTC end hour" hint="Equal start and end means all day.">
            <Input
              type="number"
              min="0"
              max="23"
              value={String(session.end_hour_utc ?? 0)}
              onChange={(e) =>
                set("session", {
                  ...session,
                  end_hour_utc: Number(e.target.value),
                })
              }
            />
          </Field>
          <div className="form-full category-options">
            {[
              "Monday",
              "Tuesday",
              "Wednesday",
              "Thursday",
              "Friday",
              "Saturday",
              "Sunday",
            ].map((day, index) => (
              <label className="toggle-field" key={day}>
                <input
                  type="checkbox"
                  checked={asArray<number>(session.weekdays).includes(index)}
                  onChange={(e) =>
                    set("session", {
                      ...session,
                      weekdays: e.target.checked
                        ? [...asArray<number>(session.weekdays), index]
                        : asArray<number>(session.weekdays).filter(
                            (v) => v !== index,
                          ),
                    })
                  }
                />
                {day}
              </label>
            ))}
          </div>
        </div>
      )}
      <div className="form-section">Exit management & simulation</div>
      <div className="form-grid">
        <Field label="Trailing stop">
          <Select
            value={String(exits.trailing_method ?? "NONE")}
            onChange={(e) => setExit("trailing_method", e.target.value)}
          >
            <option>NONE</option>
            <option>FIXED</option>
            <option>PERCENT</option>
            <option>ATR</option>
            <option>STRUCTURE</option>
          </Select>
        </Field>
        {typeof exits.trailing_method === "string" &&
          exits.trailing_method !== "NONE" && (
            <Field
              label="Trailing distance"
              hint="Price units for FIXED, fraction for PERCENT, ATR multiple for ATR."
            >
              <Input
                type="number"
                min="0.000001"
                step="any"
                value={String(exits.trailing_distance ?? "")}
                onChange={(e) =>
                  setExit("trailing_distance", e.target.value || null)
                }
              />
            </Field>
          )}
        <Field label="Break-even trigger (R)">
          <Input
            type="number"
            min="0.1"
            step="0.1"
            value={String(exits.break_even_trigger_r ?? "")}
            placeholder="Disabled"
            onChange={(e) =>
              setExit("break_even_trigger_r", e.target.value || null)
            }
          />
        </Field>
        <Field label="Maximum holding bars">
          <Input
            type="number"
            min="1"
            step="1"
            value={String(exits.max_holding_bars ?? "")}
            placeholder="No time-based exit"
            onChange={(e) =>
              setExit(
                "max_holding_bars",
                e.target.value ? Number(e.target.value) : null,
              )
            }
          />
        </Field>
        <Field label="Entry execution model">
          <Select
            value={String(execution.entry_type ?? "MARKET")}
            onChange={(e) => setExecution("entry_type", e.target.value)}
          >
            <option>MARKET</option>
            <option>LIMIT</option>
          </Select>
        </Field>
        {execution.entry_type === "LIMIT" && (
          <Field label="Limit offset (fraction)">
            <Input
              type="number"
              min="0"
              max="0.5"
              step="0.0001"
              value={String(execution.limit_offset_fraction ?? "0.001")}
              onChange={(e) =>
                setExecution("limit_offset_fraction", e.target.value)
              }
            />
          </Field>
        )}
        <Field
          label="Volume participation cap (fraction)"
          hint="Set only with verified volume units."
        >
          <Input
            type="number"
            min="0.000001"
            max="1"
            step="any"
            value={String(execution.volume_participation_fraction ?? "")}
            placeholder="Full-fill model"
            onChange={(e) =>
              setExecution(
                "volume_participation_fraction",
                e.target.value || null,
              )
            }
          />
        </Field>
        <Field label="Exchange volume units">
          <Select
            value={String(execution.volume_unit ?? "CONTRACTS")}
            onChange={(e) => setExecution("volume_unit", e.target.value)}
          >
            <option>CONTRACTS</option>
            <option>UNDERLYING</option>
          </Select>
        </Field>
      </div>
      <label className="toggle-field">
        <input
          type="checkbox"
          checked={exits.opposite_signal_exit !== false}
          onChange={(e) => setExit("opposite_signal_exit", e.target.checked)}
        />
        Exit on an opposite closed-candle signal
      </label>
      <div className="form-grid">
        <Field
          label="Paper model maintenance margin (fraction)"
          hint="An explicit internal liquidation assumption; this does not verify Delta margin tiers."
        >
          <Input
            type="number"
            min="0.000001"
            max="0.999999"
            step="any"
            value={String(execution.maintenance_margin_rate ?? "")}
            onChange={(event) =>
              setExecution(
                "maintenance_margin_rate",
                event.target.value || null,
              )
            }
          />
        </Field>
        <Field
          label="Minimum modeled liquidation distance (fraction)"
          hint="Requires the maintenance-margin assumption above."
        >
          <Input
            type="number"
            min="0.000001"
            max="0.999999"
            step="any"
            value={String(value.min_liquidation_distance_fraction ?? "")}
            onChange={(event) =>
              set(
                "min_liquidation_distance_fraction",
                event.target.value || null,
              )
            }
          />
        </Field>
      </div>
      <div className="form-section">Partial profit targets</div>
      {targets.map((target, index) => (
        <div className="form-grid" key={index}>
          <Field label={`Target ${index + 1} reward / risk multiple`}>
            <Input
              type="number"
              min="0.1"
              step="0.1"
              value={String(target.reward_multiple)}
              onChange={(e) =>
                setExit(
                  "targets",
                  targets.map((v, i) =>
                    i === index ? { ...v, reward_multiple: e.target.value } : v,
                  ),
                )
              }
            />
          </Field>
          <Field label={`Target ${index + 1} position fraction`}>
            <Input
              type="number"
              min="0.000001"
              max="1"
              step="any"
              value={String(target.fraction)}
              onChange={(e) =>
                setExit(
                  "targets",
                  targets.map((v, i) =>
                    i === index ? { ...v, fraction: e.target.value } : v,
                  ),
                )
              }
            />
          </Field>
          <Button
            type="button"
            variant="ghost"
            onClick={() =>
              setExit(
                "targets",
                targets.filter((_, i) => i !== index),
              )
            }
          >
            Remove target
          </Button>
        </div>
      ))}
      <Button
        type="button"
        variant="secondary"
        disabled={targets.length >= 10}
        onClick={() =>
          setExit("targets", [
            ...targets,
            {
              reward_multiple: String(targets.length + 1),
              fraction: targets.length ? "0.5" : "1",
            },
          ])
        }
      >
        Add partial target
      </Button>
      <p className="list-note">
        Target fractions must sum exactly to one and reward multiples must
        increase. With no partial targets, the configured full-position take
        profit applies.
      </p>
      <p className="list-note">
        Custom regime thresholds and liquidation assumptions use the advanced
        schema. Supported deployment modes are checked by the backend for this
        exact configuration.
      </p>
    </>
  );
}
