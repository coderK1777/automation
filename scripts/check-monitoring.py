"""Read-only acceptance of the optional Prometheus/Grafana stack.

Reads the private local Grafana password internally and never prints it, an
authorization header or a complete configuration. Creates no orders, users,
alerts, deliveries or dashboards; it checks the provisioned infrastructure.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import time

import httpx

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prometheus", default="http://127.0.0.1:9090")
    parser.add_argument("--grafana", default="http://127.0.0.1:3001")
    parser.add_argument("--password-file", type=Path, default=ROOT/".runtime"/"monitoring"/"grafana-password")
    parser.add_argument("--timeout", type=int, default=60)
    args = parser.parse_args()
    password = args.password_file.read_text().strip()
    deadline = time.monotonic()+max(1, min(args.timeout, 300))
    last_error = "not_started"
    with httpx.Client(timeout=10, trust_env=False) as client:
        while time.monotonic() < deadline:
            try:
                healthy = client.get(args.prometheus+"/-/healthy")
                healthy.raise_for_status()
                targets = client.get(args.prometheus+"/api/v1/targets").json()
                target = next(row for row in targets["data"]["activeTargets"] if row["labels"].get("job") == "tradebot-api")
                assert target["health"] == "up", "API scrape is not healthy"
                rules = client.get(args.prometheus+"/api/v1/rules").json()
                actual_rules = [rule for group in rules["data"]["groups"] for rule in group["rules"]]
                assert len(actual_rules) >= 11 and all(rule["health"] == "ok" for rule in actual_rules)
                metrics = {}
                for expression in ("tradebot_database_available", "tradebot_worker_healthy", "tradebot_backup_last_attempt_success",
                                   "tradebot_offsite_backup_last_success_age_seconds"):
                    result = client.get(args.prometheus+"/api/v1/query", params={"query": expression}).json()
                    values = result["data"]["result"]
                    assert values, "Expected metric has not been scraped"
                    metrics[expression] = float(values[0]["value"][1])
                assert metrics["tradebot_database_available"] == 1
                assert metrics["tradebot_worker_healthy"] == 1
                assert metrics["tradebot_backup_last_attempt_success"] == 1
                with httpx.Client(timeout=10, trust_env=False, auth=("operator", password)) as grafana:
                    health = grafana.get(args.grafana+"/api/health")
                    health.raise_for_status()
                    assert health.json().get("database") == "ok"
                    dashboard = grafana.get(args.grafana+"/api/dashboards/uid/tradebot-operations")
                    dashboard.raise_for_status()
                    assert dashboard.json()["meta"]["provisioned"] is True
                    assert len(dashboard.json()["dashboard"]["panels"]) == 12
                    datasource = grafana.get(args.grafana+"/api/datasources/uid/tradebot-prometheus/health")
                    datasource.raise_for_status()
                    assert datasource.json().get("status") == "OK"
                observed = datetime.now(timezone.utc)
                evidence = {"observed_at": observed.isoformat(), "verification": "actual-local-containers",
                        "checks": {"prometheus_health": "passed", "api_metrics_scrape": "passed", "11_alert_rules": "passed",
                                   "grafana_authenticated_health": "passed", "12_panel_provisioned_dashboard": "passed",
                                   "grafana_prometheus_datasource": "passed"}, "observed_metrics": metrics,
                        "limitations": ["Local evaluated alerts do not establish authorized external delivery.",
                                        "Offsite backup state remains unknown until actual configured delivery.",
                                        "A real VPS/domain/certificate deployment was not performed."]}
                destination = ROOT/"docs"/"validation"/("monitoring-runtime-"+observed.date().isoformat()+".json")
                destination.write_text(json.dumps(evidence, indent=2)+"\n")
                print("Prometheus API scrape/rules and authenticated Grafana dashboard/datasource passed; evidence saved.")
                return
            except Exception as error:
                last_error = type(error).__name__
                time.sleep(2)
    print("Monitoring acceptance did not pass ("+last_error+"); no credentials were printed.")
    raise SystemExit(1)


if __name__ == "__main__":
    main()
