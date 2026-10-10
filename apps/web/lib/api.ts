import { useQuery } from "@tanstack/react-query";

let csrfToken: string | undefined;
export function setCsrfToken(value: string | undefined) {
  csrfToken = value;
}
export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}
function explain(value: unknown): string {
  if (typeof value === "string") return value;
  if (Array.isArray(value))
    return value
      .map((v) =>
        typeof v === "object" && v !== null && "msg" in v
          ? String(v.msg)
          : JSON.stringify(v),
      )
      .join("; ");
  if (value && typeof value === "object") return JSON.stringify(value);
  return "The request could not be completed.";
}
export async function api<T>(
  path: string,
  options: { method?: string; body?: unknown; signal?: AbortSignal } = {},
): Promise<T> {
  const method = options.method ?? "GET";
  const headers: Record<string, string> = { Accept: "application/json" };
  if (options.body !== undefined) headers["Content-Type"] = "application/json";
  if (method !== "GET" && csrfToken) headers["X-CSRF-Token"] = csrfToken;
  const response = await fetch(`/api/v1${path}`, {
    method,
    headers,
    credentials: "same-origin",
    signal: options.signal,
    body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
  });
  const contentType = response.headers.get("content-type") ?? "";
  const data: unknown = contentType.includes("json")
    ? await response.json()
    : await response.text();
  if (!response.ok) {
    const detail =
      data && typeof data === "object" && "detail" in data ? data.detail : data;
    throw new ApiError(response.status, explain(detail));
  }
  return data as T;
}
export function useApi<T>(path: string, interval = 15000) {
  return useQuery<T, Error>({
    queryKey: [path],
    queryFn: ({ signal }) => api<T>(path, { signal }),
    refetchInterval: interval,
    retry: (count, error) =>
      !(
        error instanceof ApiError && [401, 403, 404, 422].includes(error.status)
      ) && count < 1,
  });
}
export const format = {
  number(value: unknown, digits = 2) {
    const n = Number(value);
    return value === null || value === undefined || !Number.isFinite(n)
      ? "—"
      : new Intl.NumberFormat("en-IN", {
          maximumFractionDigits: digits,
          minimumFractionDigits: digits,
        }).format(n);
  },
  money(value: unknown, asset = "USD") {
    const n = Number(value);
    return value === null || value === undefined || !Number.isFinite(n)
      ? "—"
      : `${new Intl.NumberFormat("en-IN", { maximumFractionDigits: 2, minimumFractionDigits: 2 }).format(n)} ${asset}`;
  },
  date(value: unknown) {
    if (!value) return "—";
    const date = new Date(
      typeof value === "number"
        ? value < 1e12
          ? value * 1000
          : value
        : String(value),
    );
    return Number.isNaN(date.getTime())
      ? "—"
      : date.toLocaleString("en-IN", {
          timeZone: "Asia/Kolkata",
          day: "2-digit",
          month: "short",
          hour: "2-digit",
          minute: "2-digit",
        });
  },
};
export function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}
export function asArray<T>(value: unknown): T[] {
  return Array.isArray(value) ? (value as T[]) : [];
}
