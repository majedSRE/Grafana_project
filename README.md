# OctoSight - Unified Cloud Observability Platform

"Many signals, one view."

OctoSight is an Azure-hosted observability platform for metrics, logs, traces,
dashboards, and alerting. This repository contains the reusable platform and
its deployment/validation tooling; it does not contain a monitored
application. External assets are optional integrations after the platform is
running.

**New here? Start with [Getting Started](docs/getting-started.md).**

## Architecture

```text
Linux host / platform metrics -> Prometheus
Container metrics -> cAdvisor -> Prometheus
Host and Docker logs -> Alloy -> Loki
External OTLP telemetry -> Alloy +-> metrics -> Prometheus
                             |       +-> traces -> Tempo
                             |       `-> logs -> Loki, when configured
Grafana -> Prometheus / Loki / Tempo
Prometheus alert rules -> Alertmanager -> Slack
```

The platform's Alloy instance collects the monitoring host's journal and
Docker logs. It does not automatically discover logs or metrics from a
different host. External assets need an appropriate integration pattern and
network path; see [Connect an Asset](docs/connect-an-asset.md).

Grafana is bound to the host loopback interface for user access through an
SSH tunnel. OTLP receivers are private ingestion endpoints. Prometheus,
Loki, Tempo, and Alertmanager are not public user-facing endpoints.

## Repository contents

| Location | Purpose |
| --- | --- |
| `terraform/` | Azure infrastructure definition, inputs, outputs, and tests |
| `docker-compose.yml` | Pinned OctoSight services, storage, networking, and limits |
| `config/` | Prometheus, Grafana, Loki, Tempo, Alloy, and Alertmanager configuration |
| `host/` | Docker mount guard, journal settings, Node Exporter, and host firewall |
| `scripts/` | Platform operation, readiness, and synthetic telemetry validation |
| `tests/` | Offline tests and isolated Docker validation setup |
| `docs/` | Deployment journey, host runbook, operations, verification, and capacity notes |

Persistent service data is kept under `/opt/observability-data` on the host,
outside the repository. Grafana's admin password and the Slack webhook are
operator-provided files required by the current Compose/Alertmanager
configuration. Never commit secrets, private keys, `.env` files,
Terraform state, or telemetry data.

## After successful deployment

The normal journey is documented in [Getting Started](docs/getting-started.md).
The detailed Linux procedure is [Azure and Linux](docs/azure-and-linux.md).
For day-2 operation, use [Operations](docs/operations.md). Evidence and its
limitations are recorded in [Verification](docs/verification.md), and the
short-lived demo sizing assumptions are in [Capacity](docs/capacity.md).

When the platform is running, Grafana is accessed through the tunnel produced
by Terraform:

```text
ssh -N -L 3000:127.0.0.1:3000 grafana@<MONITORING_PUBLIC_IP>
```

Then open `http://localhost:3000`. Connecting an external asset is optional
and should begin only after the platform validation in Getting Started passes.
