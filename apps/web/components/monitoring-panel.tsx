"use client";
import { asRecord, format, useApi } from "@/lib/api";
import { Alert, Badge, Card, ErrorState, JsonView, Loading } from "./ui";
export default function MonitoringPanel() {
  const query = useApi<Record<string, unknown>>("/monitoring", 15000);
  const d = query.data ?? {},
    backup = asRecord(d.backup),
    resource = asRecord(d.resource);
  return (
    <Card
      className="space-top"
      title="Operational monitoring evidence"
      subtitle="Measurements are recorded by the running services; missing observations remain unknown"
    >
      <div className="panel-body">
        {query.isLoading ? (
          <Loading />
        ) : query.error ? (
          <ErrorState error={query.error} />
        ) : (
          <>
            <div className="health-row">
              <span>Last successful backup</span>
              <strong>{format.date(backup.last_success_at)}</strong>
            </div>
            <div className="health-row">
              <span>Latest backup attempt</span>
              <Badge
                tone={
                  backup.last_attempt_success === true
                    ? "green"
                    : backup.last_attempt_success === false
                      ? "red"
                      : "neutral"
                }
              >
                {backup.last_attempt_success === true
                  ? "SUCCEEDED"
                  : backup.last_attempt_success === false
                    ? "FAILED"
                    : "NOT OBSERVED"}
              </Badge>
            </div>
            <div className="health-row">
              <span>API resident memory</span>
              <strong>
                {resource.api_resident_memory_bytes == null
                  ? "—"
                  : `${format.number(Number(resource.api_resident_memory_bytes) / 1048576)} MiB`}
              </strong>
            </div>
            <div className="health-row">
              <span>Host load (1 minute)</span>
              <strong>{format.number(resource.host_load_1m)}</strong>
            </div>
            {backup.last_success_at == null && (
              <Alert tone="amber">
                No successful backup observation is recorded. Verify recovery
                using a preserved database snapshot.
              </Alert>
            )}
            <details>
              <summary>
                Reconciliation, restart and acknowledgement measurements
              </summary>
              <JsonView value={d} />
            </details>
          </>
        )}
      </div>
    </Card>
  );
}
