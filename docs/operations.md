# Operating the OctoSight platform

All Linux commands run on the monitoring VM from `/opt/observability`, with
Docker invoked through `sudo`.

## Health and evidence

Readiness proves that services can respond. The telemetry probe also writes
synthetic records and verifies their actual contents:

```bash
cd /opt/observability
sudo bash scripts/platform.sh status
sudo bash scripts/platform.sh verify
sudo bash scripts/platform.sh verify-datasources
sudo bash scripts/platform.sh test-telemetry
```

The probe saves a unique run record in `validation-results/`. It checks a
cumulative counter, a gauge, a unique log, and two spans with a parent/child
relationship. It treats rejected OTLP records as failure. Query credentials
and response bodies are not printed on errors.

The self-monitoring jobs are `prometheus`, `linux-server`, `cadvisor`,
`grafana`, `loki`, `tempo`, and `alloy`. After the full platform is started,
`up` should show every target as `1`.

## Alerting scope and verification

Prometheus evaluates `config/prometheus/alerts.yml` and forwards firing alerts
to Alertmanager at `alertmanager:9093`. The current receiver is
`slack-observer`; it uses the operator-provided Slack webhook file configured
through `SLACK_WEBHOOK_FILE`. Never put the webhook URL in the repository or
in a command copied into chat.

Current platform rules and thresholds:

- `MonitoringTargetDown` — any scrape target down for two minutes.
- `MonitoringBackendDown` — expected backend scrape series absent for two
  minutes.
- `HostHighCpu` — host CPU above 85% for ten minutes.
- `HostLowMemory` — less than 10% host memory available for five minutes.
- `HostFilesystemFilling` — less than 15% filesystem space available for ten
  minutes, excluding `tmpfs` and `overlay`.
- `PrometheusRuleEvaluationFailures` — a rule-evaluation failure recorded in
  the last ten minutes.
- `PrometheusNotificationErrors` — a notification error recorded in the last
  ten minutes.
- `PrometheusNotificationsDropped` — a notification dropped in the last ten
  minutes.
- `PrometheusTSDBCompactionFailures` — a TSDB compaction failure recorded in
  the last ten minutes.
- `AlloyConfigLoadFailure` — an Alloy configuration-load failure recorded in
  the last ten minutes.
- `CAdvisorScrapeFailure` — cAdvisor reports a scrape error for five minutes.
- `ContainerHighMemory` — an observability Compose service exceeds 90% of its
  configured cAdvisor memory limit for ten minutes.
- `LokiWALCorruption` — a Loki WAL corruption recorded in the last ten
  minutes.
- `LokiWALDiskFull` — a Loki WAL disk-full failure recorded in the last ten
  minutes.
- `LokiChunkFlushFailures` — a Loki chunk-flush failure recorded in the last
  ten minutes.

Application rules are generic and require an appropriately instrumented
workload; they are not a platform dependency:

- `ApplicationServiceHighErrorRate` — more than 5% failed server transactions
  for five minutes, independently grouped by `service_name`.
- `ApplicationServiceHighLatency` — server-transaction P95 latency above 0.5
  seconds for five minutes, independently grouped by `service_name`.
- `ApplicationTransactionFailures` — at least one failed server transaction in
  the most recent five-minute window, independently grouped by `service_name`.

Before testing alerts, verify normal telemetry first. Then inspect Prometheus
and Alertmanager state without changing the rules:

```bash
cd /opt/observability
sudo bash scripts/platform.sh verify
sudo docker compose ps prometheus alertmanager
sudo docker compose logs --tail 100 prometheus alertmanager
```

Do not treat an application alert as testable until the relevant workload has
non-empty request, error, and latency telemetry.

## Validate host and container logs

A journal entry proves the host mount and permissions work:

```bash
logger -t observability-validation "PLATFORM_JOURNAL_TEST_001"
journalctl --since "5 minutes ago" -t observability-validation --no-pager
```

In Grafana Explore / Loki, query:

```logql
{server="vm-monitoring-v2-test",source="systemd-journal"} |= "PLATFORM_JOURNAL_TEST_001"
```

Keep the synthetic Docker logger alive long enough for Alloy discovery:

```bash
sudo docker run --rm --network observability-test \
  --label com.docker.compose.service=platform-log-validation \
  --log-driver json-file --log-opt max-size=10m --log-opt max-file=3 \
  python:3.14.7-slim-bookworm python -u -c \
  'import time; time.sleep(10); print("PLATFORM_DOCKER_TEST_001", flush=True); time.sleep(20)'
```

Query:

```logql
{server="vm-monitoring-v2-test",source="docker",service="platform-log-validation"} |= "PLATFORM_DOCKER_TEST_001"
```

Use a new marker for a later run so an old log cannot be mistaken for a new
collection result.

## Log categories

Every log stream should be classified before it is displayed:

- `log_type="system"`: host systemd/journal logs.
- `log_type="platform"`: Docker logs from OctoSight services and validation helpers.
- `log_type="application"`: Docker logs from an instrumented external workload.

Starting Explore queries are:

```logql
{log_type=~"system|platform"}
{log_type="application"}
```

Avoid sending the same external application log stream through both Docker
collection and OTLP unless duplication is intentional. Preserve structured
trace and span identifiers when the workload emits them.

## Stop, start, and verify persistence

Graceful stopping lets the backends flush their state. These commands preserve
volumes and bind-mounted data:

```bash
cd /opt/observability
sudo bash scripts/platform.sh test-telemetry
sudo bash scripts/platform.sh stop
sudo bash scripts/platform.sh start all
sudo bash scripts/platform.sh verify
```

Use the actual saved filename from the first command to query the same records
without re-emitting them:

```bash
sudo bash scripts/platform.sh verify-record probe-YYYYMMDDTHHMMSS.json
```

Repeat this verification after a planned VM reboot. Confirm the data disk is
mounted and Docker and Node Exporter are active. Do not test disk failure by
detaching or unmounting the live disk.

## Diagnose before changing configuration

Start with state, then logs, then the failing layer's configuration:

```bash
cd /opt/observability
sudo docker compose ps --all
sudo docker compose logs --tail 100 prometheus
sudo docker compose logs --tail 100 loki
sudo docker compose logs --tail 100 tempo
sudo docker compose logs --tail 100 alloy
sudo docker stats --no-stream
free -h
df -h / /opt/observability-data
sudo journalctl -u node-exporter -n 50 --no-pager
```

Choose only the relevant component log command. Inspect one failure, make one
change, validate it, and retest. Grafana datasource URLs are container DNS
names, never `localhost`.

Loki can initially return HTTP 503 during its readiness grace period even
when queries already work. The layer-start helper retries readiness for a
bounded window. Inspect the response and logs before changing configuration.

Alloy's OTLP queues are bounded and in memory. Short backend interruptions can
be retried, but crashes, long outages, and overload can lose in-flight
telemetry. Stored data in Prometheus/Loki/Tempo remains independent of Alloy
availability. This TEST deployment does not guarantee lossless ingestion.

## Configuration and image updates

`images.lock.yml` contains image references/digests and can be committed after
review. The helper uses it whenever it exists; preserve it with the matching
configuration revision. Raw Compose commands must also include `-f
images.lock.yml` when the exact digest-pinned deployment is required.

Before an intentional upgrade, take the cold backup described in the host
runbook, retain the old configuration and lock file, review upstream upgrade
instructions, update one component, and rerun its checks. Do not overwrite
`.env` or the Grafana secret. Changing the bootstrap secret does not rotate an
existing Grafana password automatically.

## Safe local failure drill

The local validation stack supports bounded restart drills. These affect only
the named service in the `observability-validation` project and never remove
volumes:

```text
python scripts/local_failure_drill.py alertmanager --seconds 10
python scripts/local_failure_drill.py loki --seconds 10
```

Run readiness and telemetry probes after a drill. Do not use destructive
container removal or volume deletion as a normal recovery test.

## Ingestion contract for later assets

- OTLP/gRPC uses the private monitoring address configured in `.env` on port
  `4317`.
- OTLP/HTTP uses that address on port `4318`, with `/v1/metrics`, `/v1/logs`,
  and `/v1/traces` as applicable.
- OTLP metrics initially use cumulative temporality. No experimental delta
  conversion is enabled.
- Preserve `service.name`, optional `service.namespace`, and
  `deployment.environment.name` resource attributes.
- Loki native OTLP index labels use normalized names such as `service_name`.
  Trace IDs and instance identifiers remain metadata, not index labels.
- Do not send the same application's logs through both Docker tailing and OTLP
  unless duplication is intentional.

Backend ports are not public ingestion endpoints. Asset installation and SDK
configuration are handled outside this repository.
