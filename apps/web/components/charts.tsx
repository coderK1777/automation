"use client";
import { useEffect, useRef } from "react";
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  BarChart,
  Bar,
} from "recharts";
import {
  createChart,
  CandlestickSeries,
  HistogramSeries,
  ColorType,
  type UTCTimestamp,
} from "lightweight-charts";
import type { Candle, Point } from "@/lib/types";
import { format } from "@/lib/api";
import { Empty } from "./ui";
export function EquityChart({
  points,
  asset = "USD",
}: {
  points: Point[];
  asset?: string;
}) {
  if (!points.length)
    return (
      <Empty
        title="An equity curve starts with recorded activity"
        description="Create a simulated account and run a strategy. Historical balance samples will appear here."
      />
    );
  const data = points.map((point, index) => ({
    label:
      point.timestamp || point.time || point.created_at
        ? format.date(point.timestamp ?? point.time ?? point.created_at)
        : String(index + 1),
    equity: Number(point.equity),
  }));
  return (
    <div className="chart-container">
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart
          data={data}
          margin={{ left: 12, right: 20, top: 10, bottom: 0 }}
        >
          <defs>
            <linearGradient id="equityFill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#548eff" stopOpacity={0.24} />
              <stop offset="100%" stopColor="#548eff" stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid
            vertical={false}
            stroke="#23324a"
            strokeDasharray="3 5"
          />
          <XAxis
            dataKey="label"
            tick={{ fill: "#6d839f", fontSize: 9 }}
            axisLine={false}
            tickLine={false}
            minTickGap={90}
          />
          <YAxis
            width={62}
            tick={{ fill: "#6d839f", fontSize: 9 }}
            axisLine={false}
            tickLine={false}
            domain={["auto", "auto"]}
            tickFormatter={(v) => format.number(v, 0)}
          />
          <Tooltip
            content={({ active, payload, label }) =>
              active && payload?.length ? (
                <div className="tooltip">
                  <p>{String(label)}</p>
                  <strong>{format.money(payload[0].value, asset)}</strong>
                </div>
              ) : null
            }
          />
          <Area
            type="monotone"
            dataKey="equity"
            stroke="#6495ff"
            strokeWidth={2}
            fill="url(#equityFill)"
            isAnimationActive={false}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
export function ComparisonChart({
  data,
}: {
  data: { name: string; value: number }[];
}) {
  if (!data.length) return <Empty title="No completed backtests to compare" />;
  return (
    <div className="chart-container">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ left: 5, right: 20 }}>
          <CartesianGrid
            vertical={false}
            stroke="#23324a"
            strokeDasharray="3 5"
          />
          <XAxis
            dataKey="name"
            tick={{ fill: "#8d9db3", fontSize: 10 }}
            tickLine={false}
            axisLine={false}
          />
          <YAxis
            tick={{ fill: "#8d9db3", fontSize: 9 }}
            tickLine={false}
            axisLine={false}
          />
          <Tooltip
            contentStyle={{
              background: "#15253b",
              border: "1px solid #395071",
              fontSize: 11,
            }}
          />
          <Bar
            dataKey="value"
            fill="#ac91f8"
            radius={[4, 4, 0, 0]}
            isAnimationActive={false}
          />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
export function CandleChart({ candles }: { candles: Candle[] }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!ref.current || !candles.length) return;
    const chart = createChart(ref.current, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: "#111a28" },
        textColor: "#8294ad",
        fontSize: 10,
      },
      grid: {
        vertLines: { color: "#1d2b3e" },
        horzLines: { color: "#1d2b3e" },
      },
      rightPriceScale: { borderColor: "#26364f" },
      timeScale: {
        borderColor: "#26364f",
        timeVisible: true,
        secondsVisible: false,
      },
      crosshair: {
        vertLine: { color: "#4a678c" },
        horzLine: { color: "#4a678c" },
      },
    });
    const series = chart.addSeries(CandlestickSeries, {
      upColor: "#48d4a2",
      downColor: "#f47786",
      borderVisible: false,
      wickUpColor: "#48d4a2",
      wickDownColor: "#f47786",
    });
    const volume = chart.addSeries(HistogramSeries, {
      priceFormat: { type: "volume" },
      priceScaleId: "",
      color: "#3b6086",
    });
    volume
      .priceScale()
      .applyOptions({ scaleMargins: { top: 0.85, bottom: 0 } });
    const timeOf = (c: Candle) => {
      const value = c.time ?? c.timestamp;
      return (
        typeof value === "string"
          ? Math.floor(new Date(value).getTime() / 1000)
          : Number(value)
      ) as UTCTimestamp;
    };
    const valid = candles
      .filter((c) => Number.isFinite(timeOf(c)))
      .sort((a, b) => timeOf(a) - timeOf(b));
    const distinct = valid.filter(
      (c, i) => i === 0 || timeOf(c) > timeOf(valid[i - 1]),
    );
    series.setData(
      distinct.map((c) => ({
        time: timeOf(c),
        open: Number(c.open),
        high: Number(c.high),
        low: Number(c.low),
        close: Number(c.close),
      })),
    );
    volume.setData(
      distinct.map((c) => ({
        time: timeOf(c),
        value: Number(c.volume),
        color: Number(c.close) >= Number(c.open) ? "#285c5080" : "#63323f80",
      })),
    );
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [candles]);
  if (!candles.length)
    return (
      <Empty
        title="No historical candles stored"
        description="Import verified Delta India candles to view this instrument."
      />
    );
  return (
    <div
      ref={ref}
      className="terminal-chart"
      aria-label="Interactive historical Delta India candlestick and volume chart"
    />
  );
}
