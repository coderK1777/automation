import { Suspense } from "react";
import { notFound } from "next/navigation";
import TradingApp from "@/components/trading-app";
const sections = [
  "dashboard",
  "terminal",
  "strategies",
  "backtesting",
  "paper",
  "positions",
  "orders",
  "risk",
  "performance",
  "journal",
  "notifications",
  "integrations",
  "system-health",
  "settings",
  "demo",
  "live",
  "research",
  "news",
  "reports",
];
export default async function Page({
  params,
}: {
  params: Promise<{ section: string }>;
}) {
  const { section } = await params;
  if (!sections.includes(section)) notFound();
  return (
    <Suspense>
      <TradingApp section={section} />
    </Suspense>
  );
}
