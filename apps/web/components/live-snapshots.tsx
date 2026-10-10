"use client";
import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { z } from "zod";
import { Radio } from "lucide-react";
import type { Dashboard } from "@/lib/types";
const snapshot = z
  .object({
    mode: z.literal("PAPER"),
    live_enabled: z.literal(false),
    account: z.union([z.record(z.string(), z.unknown()), z.null()]),
    metrics: z.record(z.string(), z.unknown()),
    strategies: z.array(z.unknown()),
    positions: z.array(z.unknown()),
    recent_orders: z.array(z.unknown()),
    equity_curve: z.array(z.unknown()),
    worker: z.record(z.string(), z.unknown()),
    market_data: z.record(z.string(), z.unknown()),
  })
  .passthrough();
export default function LiveSnapshots() {
  const client = useQueryClient();
  const [status, setStatus] = useState("Connecting");
  useEffect(() => {
    let source: EventSource | undefined;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let closed = false;
    let attempts = 0;
    let sequence = 0;
    function connect() {
      if (closed) return;
      source = new EventSource("/api/v1/events", { withCredentials: true });
      source.onopen = () => {
        attempts = 0;
        sequence = 0;
        setStatus("Snapshot stream connected");
      };
      source.addEventListener("snapshot", (event: MessageEvent<string>) => {
        try {
          const next = Number(event.lastEventId);
          if (next && next <= sequence) return;
          const validated = snapshot.parse(JSON.parse(event.data));
          sequence = next;
          client.setQueryData(
            ["/dashboard"],
            validated as unknown as Dashboard,
          );
          setStatus("Snapshot stream connected");
        } catch {
          setStatus("Invalid snapshot · polling");
        }
      });
      source.onerror = () => {
        source?.close();
        setStatus("Stream reconnecting · polling");
        const delay =
          Math.min(30000, 1000 * 2 ** Math.min(attempts++, 5)) +
          Math.random() * 500;
        timer = setTimeout(connect, delay);
      };
    }
    connect();
    return () => {
      closed = true;
      source?.close();
      if (timer) clearTimeout(timer);
    };
  }, [client]);
  return (
    <div
      className="topbar-health"
      title="Server sent account snapshots every 5 seconds; REST polling provides recovery."
    >
      <Radio size={13} />
      <span
        className={status.includes("connected") ? "text-green" : "text-amber"}
      >
        {status}
      </span>
    </div>
  );
}
