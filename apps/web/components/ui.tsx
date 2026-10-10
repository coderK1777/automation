"use client";
import {
  cloneElement,
  isValidElement,
  useId,
  type ReactElement,
  type ButtonHTMLAttributes,
  type ReactNode,
  type InputHTMLAttributes,
  type SelectHTMLAttributes,
} from "react";
import * as DialogPrimitive from "@radix-ui/react-dialog";
import {
  AlertCircle,
  CheckCircle2,
  Loader2,
  Inbox,
  X,
  ArrowUpRight,
  RefreshCw,
} from "lucide-react";
import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";
import { format } from "@/lib/api";
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
export function Button({
  className,
  variant = "primary",
  children,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "danger" | "ghost";
}) {
  return (
    <button className={cn("btn", `btn-${variant}`, className)} {...props}>
      {children}
    </button>
  );
}
export function Card({
  children,
  className,
  title,
  subtitle,
  action,
}: {
  children: ReactNode;
  className?: string;
  title?: string;
  subtitle?: string;
  action?: ReactNode;
}) {
  return (
    <section className={cn("panel", className)}>
      {title && (
        <div className="panel-heading">
          <div>
            <h2>{title}</h2>
            {subtitle && <p>{subtitle}</p>}
          </div>
          {action}
        </div>
      )}
      {children}
    </section>
  );
}
export function Badge({
  children,
  tone = "neutral",
}: {
  children: ReactNode;
  tone?: "neutral" | "blue" | "green" | "amber" | "red" | "purple";
}) {
  return <span className={cn("badge", `badge-${tone}`)}>{children}</span>;
}
export function Status({ value }: { value?: string }) {
  const upper = (value ?? "unknown").toUpperCase();
  const tone = [
    "RUNNING",
    "CONNECTED",
    "HEALTHY",
    "COMPLETED",
    "FILLED",
    "VALIDATED",
    "PAPER_APPROVED",
  ].includes(upper)
    ? "green"
    : ["FAILED", "ERROR", "REJECTED", "OFFLINE", "UNHEALTHY"].includes(upper)
      ? "red"
      : ["PAUSED", "PENDING", "DEGRADED", "STALE", "UNKNOWN"].includes(upper)
        ? "amber"
        : "neutral";
  return <Badge tone={tone}>{upper.replaceAll("_", " ")}</Badge>;
}
export function Field({
  label,
  children,
  hint,
  error,
}: {
  label: string;
  children: ReactNode;
  hint?: string;
  error?: string;
}) {
  const id = useId();
  const control = isValidElement(children)
    ? cloneElement(children as ReactElement<Record<string, unknown>>, {
        "aria-labelledby": (children.props as Record<string, unknown>)[
          "aria-label"
        ]
          ? undefined
          : `${id}-label`,
        "aria-describedby": error || hint ? `${id}-help` : undefined,
        "aria-invalid": error ? true : undefined,
      })
    : children;
  return (
    <label className="field">
      <span id={`${id}-label`}>{label}</span>
      {control}
      {error ? (
        <small id={`${id}-help`} className="text-red">
          {error}
        </small>
      ) : hint ? (
        <small id={`${id}-help`}>{hint}</small>
      ) : null}
    </label>
  );
}
export function Input({
  className,
  ...props
}: InputHTMLAttributes<HTMLInputElement>) {
  return <input className={cn("input", className)} {...props} />;
}
export function Select({
  className,
  ...props
}: SelectHTMLAttributes<HTMLSelectElement>) {
  return <select className={cn("input", className)} {...props} />;
}
export function Textarea(
  props: React.TextareaHTMLAttributes<HTMLTextAreaElement>,
) {
  return <textarea {...props} className={cn("input", props.className)} />;
}
export function Alert({
  children,
  tone = "amber",
}: {
  children: ReactNode;
  tone?: "amber" | "red" | "blue" | "green";
}) {
  return (
    <div
      role={tone === "red" ? "alert" : "status"}
      className={cn("alert", `alert-${tone}`)}
    >
      {tone === "green" ? (
        <CheckCircle2 size={17} />
      ) : (
        <AlertCircle size={17} />
      )}
      <div>{children}</div>
    </div>
  );
}
export function Empty({
  title,
  description,
  action,
}: {
  title: string;
  description?: string;
  action?: ReactNode;
}) {
  return (
    <div className="empty">
      <div className="empty-icon">
        <Inbox size={26} />
      </div>
      <h3>{title}</h3>
      {description && <p>{description}</p>}
      {action}
    </div>
  );
}
export function Loading({
  label = "Loading current records…",
}: {
  label?: string;
}) {
  return (
    <div className="loading" role="status">
      <Loader2 className="spin" size={20} />
      {label}
    </div>
  );
}
export function ErrorState({
  error,
  retry,
}: {
  error: Error | null;
  retry?: () => void;
}) {
  return (
    <Alert tone="red">
      <strong>Unable to load data</strong>
      <p>{error?.message ?? "Backend unavailable."}</p>
      {retry && (
        <Button variant="secondary" onClick={retry}>
          <RefreshCw size={14} />
          Try again
        </Button>
      )}
    </Alert>
  );
}
export function Metric({
  label,
  value,
  subtext,
  icon,
  trend,
}: {
  label: string;
  value: ReactNode;
  subtext?: ReactNode;
  icon?: ReactNode;
  trend?: boolean;
}) {
  return (
    <div className="metric panel">
      <div className="metric-label">
        {label}
        {icon}
      </div>
      <div
        className={cn(
          "metric-value",
          trend === true
            ? "text-green"
            : trend === false
              ? "text-red"
              : undefined,
        )}
      >
        {value}
      </div>
      <div className="metric-sub">{subtext ?? "Current paper account"}</div>
    </div>
  );
}
export function PageTitle({
  eyebrow,
  title,
  description,
  action,
}: {
  eyebrow?: string;
  title: string;
  description: string;
  action?: ReactNode;
}) {
  return (
    <div className="page-heading">
      <div>
        {eyebrow && <div className="eyebrow">{eyebrow}</div>}
        <h1>{title}</h1>
        <p>{description}</p>
      </div>
      {action && <div className="heading-actions">{action}</div>}
    </div>
  );
}
export function DataTable<T>({
  rows,
  columns,
  empty = "No records yet.",
  getKey,
}: {
  rows: T[];
  columns: {
    label: string;
    render: (row: T) => ReactNode;
    className?: string;
  }[];
  empty?: string;
  getKey: (row: T, index: number) => string | number;
}) {
  if (!rows.length) return <Empty title={empty} />;
  return (
    <div className="table-scroll">
      <table>
        <thead>
          <tr>
            {columns.map((c) => (
              <th key={c.label} className={c.className}>
                {c.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={getKey(row, index)}>
              {columns.map((c) => (
                <td key={c.label} className={c.className}>
                  {c.render(row)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
export function Dialog({
  open,
  onOpenChange,
  title,
  description,
  children,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description?: string;
  children: ReactNode;
}) {
  return (
    <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="dialog-overlay" />
        <DialogPrimitive.Content className="dialog-content">
          <DialogPrimitive.Title className="dialog-title">
            {title}
          </DialogPrimitive.Title>
          {description ? (
            <DialogPrimitive.Description className="dialog-description">
              {description}
            </DialogPrimitive.Description>
          ) : (
            <DialogPrimitive.Description className="sr-only">
              {title} controls
            </DialogPrimitive.Description>
          )}
          <DialogPrimitive.Close
            className="dialog-close"
            aria-label="Close dialog"
          >
            <X size={20} />
          </DialogPrimitive.Close>
          {children}
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}
export function JsonView({ value }: { value: unknown }) {
  return <pre className="json-view">{JSON.stringify(value, null, 2)}</pre>;
}
export function Freshness({
  updatedAt,
  fetching,
}: {
  updatedAt: number;
  fetching?: boolean;
}) {
  const old = updatedAt > 0 && Date.now() - updatedAt > 60000;
  return (
    <span className={cn("freshness", old && "text-amber")}>
      {fetching ? (
        <RefreshCw size={12} className="spin" />
      ) : (
        <span className={cn("status-dot", old ? "dot-amber" : "dot-green")} />
      )}{" "}
      {updatedAt
        ? `Updated ${format.date(updatedAt)} IST`
        : "Waiting for snapshot"}
      {old ? " · snapshot may be stale" : ""}
    </span>
  );
}
export function SectionLink({
  href,
  children,
}: {
  href: string;
  children: ReactNode;
}) {
  return (
    <a className="section-link" href={href}>
      {children}
      <ArrowUpRight size={15} />
    </a>
  );
}
