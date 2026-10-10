"use client";
import { Plus, X } from "lucide-react";
import { Button, Field, Input, Select } from "./ui";
export type Operand = { feature?: string; value?: string };
export type Rule = {
  op: string;
  left?: Operand;
  right?: Operand;
  children?: Rule[];
};
const features = [
  "close",
  "open",
  "high",
  "low",
  "volume",
  "ema_fast",
  "ema_slow",
  "sma",
  "rsi",
  "upper_band",
  "lower_band",
  "atr",
  "atr_fraction",
  "adx",
  "vwap",
  "volume_average",
  "support",
  "resistance",
  "momentum",
  "bullish_engulfing",
  "bearish_engulfing",
  "body_fraction",
  "wma",
  "macd_line",
  "macd_signal",
  "macd_histogram",
  "stochastic_k",
  "stochastic_d",
  "donchian_upper",
  "donchian_lower",
  "keltner_upper",
  "keltner_lower",
  "supertrend",
  "supertrend_direction",
  "pivot",
  "pivot_r1",
  "pivot_s1",
  "plus_di",
  "minus_di",
];
export function RuleBuilder({
  label,
  value,
  onChange,
}: {
  label: string;
  value: Rule;
  onChange: (rule: Rule) => void;
}) {
  const children = value.children ?? [];
  const change = (index: number, rule: Rule) =>
    onChange({
      ...value,
      children: children.map((v, i) => (i === index ? rule : v)),
    });
  return (
    <div
      style={{
        border: "1px solid #2b3a52",
        padding: 15,
        borderRadius: 7,
        marginTop: 15,
      }}
    >
      <div className="small-row" style={{ marginBottom: 14 }}>
        <strong style={{ color: "#c0d1ea" }}>{label}</strong>
        <Select
          aria-label={`${label} condition grouping`}
          value={value.op}
          onChange={(e) => onChange({ ...value, op: e.target.value })}
          style={{ width: 130, minHeight: 31 }}
        >
          <option value="AND">All conditions</option>
          <option value="OR">Any condition</option>
        </Select>
      </div>
      {children.map((child, index) => {
        const negated = child.op === "NOT";
        const rule = negated ? (child.children?.[0] ?? child) : child;
        const update = (next: Rule) =>
          change(index, negated ? { op: "NOT", children: [next] } : next);
        return (
          <div
            key={index}
            style={{
              paddingTop: index ? 15 : 0,
              borderTop: index ? "1px solid #223044" : undefined,
              marginTop: index ? 15 : 0,
            }}
          >
            <div className="form-grid">
              <Field label="Indicator / price">
                <Select
                  aria-label={`${label} left operand ${index + 1}`}
                  value={rule.left?.feature ?? "close"}
                  onChange={(e) =>
                    update({ ...rule, left: { feature: e.target.value } })
                  }
                >
                  {features.map((f) => (
                    <option key={f}>{f}</option>
                  ))}
                </Select>
              </Field>
              <Field label="Comparison">
                <Select
                  aria-label={`${label} comparison ${index + 1}`}
                  value={rule.op}
                  onChange={(e) => update({ ...rule, op: e.target.value })}
                >
                  {[
                    "GT",
                    "GTE",
                    "LT",
                    "LTE",
                    "EQ",
                    "CROSS_ABOVE",
                    "CROSS_BELOW",
                  ].map((op) => (
                    <option key={op} value={op}>
                      {op.replaceAll("_", " ")}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field label="Compare with">
                <Select
                  aria-label={`${label} right operand type ${index + 1}`}
                  value={rule.right?.feature ? "feature" : "value"}
                  onChange={(e) =>
                    update({
                      ...rule,
                      right:
                        e.target.value === "feature"
                          ? { feature: "ema_slow" }
                          : { value: "30" },
                    })
                  }
                >
                  <option value="feature">Indicator / price</option>
                  <option value="value">Numeric threshold</option>
                </Select>
              </Field>
              <Field label={rule.right?.feature ? "Indicator" : "Threshold"}>
                {rule.right?.feature ? (
                  <Select
                    aria-label={`${label} right operand ${index + 1}`}
                    value={rule.right.feature}
                    onChange={(e) =>
                      update({ ...rule, right: { feature: e.target.value } })
                    }
                  >
                    {features.map((f) => (
                      <option key={f}>{f}</option>
                    ))}
                  </Select>
                ) : (
                  <Input
                    aria-label={`${label} threshold ${index + 1}`}
                    type="number"
                    step="any"
                    value={rule.right?.value ?? "30"}
                    onChange={(e) =>
                      update({ ...rule, right: { value: e.target.value } })
                    }
                    required
                  />
                )}
              </Field>
            </div>
            <div className="small-row" style={{ marginTop: 10 }}>
              <label className="toggle-field">
                <input
                  type="checkbox"
                  checked={negated}
                  onChange={(e) =>
                    change(
                      index,
                      e.target.checked ? { op: "NOT", children: [rule] } : rule,
                    )
                  }
                />
                Negate this condition (NOT)
              </label>
              <Button
                variant="ghost"
                className="btn-small"
                type="button"
                disabled={children.length <= 1}
                onClick={() =>
                  onChange({
                    ...value,
                    children: children.filter((_, i) => i !== index),
                  })
                }
                aria-label="Remove condition"
              >
                <X size={13} />
              </Button>
            </div>
          </div>
        );
      })}
      <Button
        type="button"
        variant="secondary"
        className="btn-small"
        style={{ marginTop: 15 }}
        disabled={children.length >= 8}
        onClick={() =>
          onChange({
            ...value,
            children: [
              ...children,
              {
                op: "GT",
                left: { feature: "close" },
                right: { feature: "sma" },
              },
            ],
          })
        }
      >
        <Plus size={12} />
        Add condition
      </Button>
    </div>
  );
}
export const defaultLongRules: Rule = {
  op: "AND",
  children: [
    {
      op: "CROSS_ABOVE",
      left: { feature: "ema_fast" },
      right: { feature: "ema_slow" },
    },
  ],
};
export const defaultShortRules: Rule = {
  op: "AND",
  children: [
    {
      op: "CROSS_BELOW",
      left: { feature: "ema_fast" },
      right: { feature: "ema_slow" },
    },
  ],
};
