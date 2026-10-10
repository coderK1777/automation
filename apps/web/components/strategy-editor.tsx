"use client";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import StrategyOptions, { strategyOptionKeys } from "./strategy-options";
import type { Strategy, StrategyConfig } from "@/lib/types";
import { Alert, Button, Field, Input, Select, Textarea } from "./ui";
import {
  RuleBuilder,
  defaultLongRules,
  defaultShortRules,
  type Rule,
} from "./rule-builder";
const schema = z
  .object({
    name: z.string().min(3, "Use at least 3 characters").max(100),
    description: z.string().max(2000),
    symbol: z.enum(["BTCUSD", "ETHUSD"]),
    kind: z.enum([
      "EMA_CROSS",
      "BREAKOUT",
      "MEAN_REVERSION",
      "MOMENTUM",
      "VOLATILITY",
      "SUPPORT_RESISTANCE",
      "TREND_FOLLOWING",
      "CUSTOM",
    ]),
    timeframe_seconds: z.number().int().min(60),
    fast_period: z.number().int().min(2).max(200),
    slow_period: z.number().int().min(3).max(500),
    lookback: z.number().int().min(5).max(200),
    stop_loss: z.number().positive().max(50),
    take_profit: z.number().positive().max(100),
    risk_pct: z.number().positive().max(5),
    max_leverage: z.number().min(1).max(5),
    allow_short: z.boolean(),
  })
  .refine((v) => v.kind !== "EMA_CROSS" || v.fast_period < v.slow_period, {
    path: ["slow_period"],
    message: "Slow period must be greater than fast period",
  });
type Values = z.infer<typeof schema>;
export default function StrategyEditor({
  strategy,
  onDone,
}: {
  strategy?: Strategy;
  onDone: () => void;
}) {
  const client = useQueryClient();
  const [advanced, setAdvanced] = useState(strategy?.config.kind === "CUSTOM");
  const [longRules, setLongRules] = useState<Rule>(
    (strategy?.config.long_rules as Rule) ?? defaultLongRules,
  );
  const [shortRules, setShortRules] = useState<Rule>(
    (strategy?.config.short_rules as Rule) ?? defaultShortRules,
  );
  const [advancedText, setAdvancedText] = useState(
    strategy ? JSON.stringify(strategy.config, null, 2) : "",
  );
  const [jsonError, setJsonError] = useState<string>();
  const c = strategy?.config;
  const [options, setOptions] = useState<Record<string, unknown>>(
    Object.fromEntries(
      strategyOptionKeys
        .filter((key) => c?.[key] !== undefined)
        .map((key) => [key, c?.[key]]),
    ),
  );
  const {
    register,
    handleSubmit,
    watch,
    formState: { errors },
  } = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: {
      name: strategy?.name ?? "",
      description: strategy?.description ?? "",
      symbol: (c?.symbol ?? "BTCUSD") as Values["symbol"],
      kind: (c?.kind ?? "EMA_CROSS") as Values["kind"],
      timeframe_seconds: c?.timeframe_seconds ?? 60,
      fast_period: c?.fast_period ?? 9,
      slow_period: c?.slow_period ?? 21,
      lookback: c?.lookback ?? 20,
      stop_loss: Number(c?.stop_loss_pct ?? 0.01) * 100,
      take_profit: Number(c?.take_profit_pct ?? 0.02) * 100,
      risk_pct: Number(c?.risk_fraction ?? 0.01) * 100,
      max_leverage: Number(c?.max_leverage ?? 2),
      allow_short: c?.allow_short ?? true,
    },
  });
  const kind = watch("kind");
  const save = useMutation({
    mutationFn: (body: {
      name: string;
      description: string;
      config: StrategyConfig;
    }) =>
      api<Strategy>(strategy ? `/strategies/${strategy.id}` : "/strategies", {
        method: strategy ? "PUT" : "POST",
        body,
      }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["/strategies"] });
      void client.invalidateQueries({ queryKey: ["/dashboard"] });
      onDone();
    },
  });
  const submit = (v: Values) => {
    setJsonError(undefined);
    let config: StrategyConfig = {
      ...c,
      ...options,
      ...(v.kind === "CUSTOM"
        ? {
            long_rules: longRules,
            short_rules: v.allow_short ? shortRules : null,
          }
        : {}),
      symbol: v.symbol,
      kind: v.kind,
      timeframe_seconds: v.timeframe_seconds,
      fast_period: v.fast_period,
      slow_period: v.slow_period,
      lookback: v.lookback,
      stop_loss_pct: String(v.stop_loss / 100),
      take_profit_pct: String(v.take_profit / 100),
      risk_fraction: String(v.risk_pct / 100),
      max_leverage: String(v.max_leverage),
      allow_short: v.allow_short,
    };
    if (advanced) {
      try {
        const parsed: unknown = JSON.parse(advancedText);
        if (!parsed || typeof parsed !== "object" || Array.isArray(parsed))
          throw new Error("Configuration must be a JSON object.");
        config = parsed as StrategyConfig;
      } catch (e) {
        setJsonError(e instanceof Error ? e.message : "Invalid JSON");
        return;
      }
    }
    save.mutate({ name: v.name, description: v.description, config });
  };
  return (
    <form onSubmit={handleSubmit(submit)}>
      {save.error && <Alert tone="red">{save.error.message}</Alert>}
      <div className="form-grid">
        <Field label="Strategy name" error={errors.name?.message}>
          <Input placeholder="e.g. BTC trend crossover" {...register("name")} />
        </Field>
        <Field label="Strategy model">
          <Select {...register("kind")}>
            <option value="EMA_CROSS">EMA crossover</option>
            <option value="BREAKOUT">Channel breakout</option>
            <option value="MEAN_REVERSION">Mean reversion</option>
            <option value="MOMENTUM">Price momentum</option>
            <option value="VOLATILITY">Volatility breakout</option>
            <option value="SUPPORT_RESISTANCE">Support / resistance</option>
            <option value="TREND_FOLLOWING">Trend following</option>
            <option value="CUSTOM">Custom indicator rules</option>
          </Select>
        </Field>
        <div className="form-full">
          <Field
            label="Description / research source"
            error={errors.description?.message}
          >
            <Textarea
              placeholder="Describe the hypothesis, expected market regime and source."
              {...register("description")}
            />
          </Field>
        </div>
        <Field label="Instrument">
          <Select {...register("symbol")}>
            <option>BTCUSD</option>
            <option>ETHUSD</option>
          </Select>
        </Field>
        <Field label="Candle timeframe">
          <Select {...register("timeframe_seconds", { valueAsNumber: true })}>
            <option value="60">1 minute</option>
            <option value="300">5 minutes</option>
            <option value="900">15 minutes</option>
            <option value="3600">1 hour</option>
            <option value="14400">4 hours</option>
          </Select>
        </Field>
        {kind === "EMA_CROSS" || kind === "CUSTOM" ? (
          <>
            <Field label="Fast EMA period" error={errors.fast_period?.message}>
              <Input
                type="number"
                min="2"
                max="200"
                {...register("fast_period", { valueAsNumber: true })}
              />
            </Field>
            <Field label="Slow EMA period" error={errors.slow_period?.message}>
              <Input
                type="number"
                min="3"
                max="500"
                {...register("slow_period", { valueAsNumber: true })}
              />
            </Field>
          </>
        ) : (
          <div className="form-full">
            <Field label="Lookback candles" error={errors.lookback?.message}>
              <Input
                type="number"
                min="5"
                max="200"
                {...register("lookback", { valueAsNumber: true })}
              />
            </Field>
          </div>
        )}
      </div>
      {kind === "CUSTOM" && !advanced && (
        <>
          <div className="form-section">
            Custom closed candle entry conditions
          </div>
          <RuleBuilder
            label="Long entry"
            value={longRules}
            onChange={setLongRules}
          />
          {watch("allow_short") && (
            <RuleBuilder
              label="Short entry"
              value={shortRules}
              onChange={setShortRules}
            />
          )}
          <p className="list-note">
            All indicators must be warmed up. Missing values do not generate
            signals, including negated conditions. Advanced JSON supports deeper
            nested AND / OR / NOT trees.
          </p>
        </>
      )}
      <div className="form-section">Position protection & sizing</div>
      <div className="form-grid">
        <Field label="Stop loss (%)" error={errors.stop_loss?.message}>
          <Input
            type="number"
            step="0.1"
            min="0.1"
            {...register("stop_loss", { valueAsNumber: true })}
          />
        </Field>
        <Field label="Take profit (%)" error={errors.take_profit?.message}>
          <Input
            type="number"
            step="0.1"
            min="0.1"
            {...register("take_profit", { valueAsNumber: true })}
          />
        </Field>
        <Field
          label="Account risk per trade (%)"
          hint="Also limited by independent portfolio risk settings."
          error={errors.risk_pct?.message}
        >
          <Input
            type="number"
            step="0.1"
            min="0.1"
            max="5"
            {...register("risk_pct", { valueAsNumber: true })}
          />
        </Field>
        <Field label="Maximum leverage" error={errors.max_leverage?.message}>
          <Input
            type="number"
            step="0.1"
            min="1"
            max="5"
            {...register("max_leverage", { valueAsNumber: true })}
          />
        </Field>
        <label className="toggle-field form-full">
          <input type="checkbox" {...register("allow_short")} />
          Allow short signals in paper mode
        </label>
      </div>
      {!advanced && (
        <StrategyOptions
          value={options}
          onChange={setOptions}
          baseTimeframe={watch("timeframe_seconds")}
        />
      )}
      <div className="form-section">Advanced configuration</div>
      <label className="toggle-field">
        <input
          type="checkbox"
          checked={advanced}
          onChange={(e) => {
            setAdvanced(e.target.checked);
            if (!advancedText) {
              const v = watch();
              setAdvancedText(
                JSON.stringify(
                  {
                    ...c,
                    ...options,
                    ...(v.kind === "CUSTOM"
                      ? {
                          long_rules: longRules,
                          short_rules: v.allow_short ? shortRules : null,
                        }
                      : {}),
                    symbol: v.symbol,
                    kind: v.kind,
                    timeframe_seconds: v.timeframe_seconds,
                    fast_period: v.fast_period,
                    slow_period: v.slow_period,
                    lookback: v.lookback,
                    stop_loss_pct: String(v.stop_loss / 100),
                    take_profit_pct: String(v.take_profit / 100),
                    risk_fraction: String(v.risk_pct / 100),
                    max_leverage: String(v.max_leverage),
                    allow_short: v.allow_short,
                  },
                  null,
                  2,
                ),
              );
            }
          }}
        />
        Use backend validated JSON configuration
      </label>
      {advanced && (
        <div style={{ marginTop: 14 }}>
          <Field label="Version configuration" error={jsonError}>
            <Textarea
              className="code-input"
              value={advancedText}
              onChange={(e) => setAdvancedText(e.target.value)}
            />
          </Field>
        </div>
      )}
      <p className="list-note">
        Saving creates a draft version. Validation, historical backtesting and
        paper approval are required before deployment. Editing an active
        strategy requires pausing it first.
      </p>
      <div className="form-actions">
        <Button type="button" variant="secondary" onClick={onDone}>
          Cancel
        </Button>
        <Button type="submit" disabled={save.isPending}>
          {save.isPending
            ? "Saving version…"
            : strategy
              ? "Save new version"
              : "Create strategy"}
        </Button>
      </div>
    </form>
  );
}
