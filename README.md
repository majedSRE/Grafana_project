# Unified Monitoring & Observability Platform Using Grafana

An Azure TEST monitoring foundation with Terraform infrastructure and a step-by-step Linux/platform installation. The repository contains the platform and synthetic validation tools; an external demo application's source and deployment are not part of it. Dashboards, alert rules, and Ansible are deferred.

## Start here

Start with [Terraform infrastructure](terraform/README.md) to create the Azure resources. Then follow [Azure and Linux foundation](docs/azure-and-linux.md#2-verify-the-new-host) from **section 2**, one section at a time, for the verified empty data disk, Docker, Node Exporter, storage permissions, and Grafana's secret file. The portal procedure in section 1 is an alternative to Terraform, not an additional step. These instructions target a new monitoring VM; the old environment is not a deletion target.

The target is Ubuntu 24.04 on `Standard_D8as_v5` (8 vCPUs, 32 GiB RAM), with a 64 GiB OS disk and a separate 512 GiB Premium SSD telemetry disk. The VM hosts only the monitoring platform.

## Architecture

```text
One monitoring VM:

Node Exporter + cAdvisor + platform endpoints --scraped by--> Prometheus
Linux journal + Docker logs ------------------> Alloy -----> Loki
Future OTLP ----------------------------------> Alloy --+--> Prometheus (metrics)
                                                        +--> Loki (logs)
                                                        +--> Tempo (traces)

Grafana --queries--> Prometheus / Loki / Tempo

Node Exporter runs as a systemd service; the six other services use Compose.
```

Grafana reads all three backends directly. Alloy is the collector and OTLP gateway. cAdvisor supplies per-container resource measurements; Node Exporter measures the Linux host. Neither exporter alone proves that a backend can serve queries.

Versions and service limits are pinned in the complete [docker-compose.yml](docker-compose.yml). Loki and Tempo use single-instance filesystem storage. Tempo 3 retention uses its backend compaction settings, not the removed Tempo 2 compactor configuration.

## Deployment files

| Location | Purpose |
| --- | --- |
| `terraform/` | Azure monitoring infrastructure, pinned provider, inputs, outputs and mocked plan tests |
| `docker-compose.yml` | Six platform containers, private networking, memory limits and log rotation |
| `config/` | Prometheus, Grafana datasources, Loki, Tempo and Alloy configuration |
| `host/` | Docker mount guard, journal limits, Node Exporter and its restricted host firewall |
| `scripts/` | Safe operation, readiness checks and synthetic OTLP validation |
| `tests/` | Offline tests and an isolated Docker Desktop smoke-test override |
| `docs/` | Manual deployment, operation, capacity and verification evidence |

Host data lives under `/opt/observability-data`, outside this repository. Grafana's initial admin password is read from an ignored file through a Compose secret. Never commit a real `.env`, credentials, keys, telemetry data, or Terraform state.

## After the host runbook

The helper checks the host and starts only the selected layer. Run each command, review its result, and continue only when it passes:

```bash
cd /opt/observability
sudo bash scripts/platform.sh preflight
sudo bash scripts/platform.sh validate-config
sudo bash scripts/platform.sh start metrics
sudo bash scripts/platform.sh start ui
sudo bash scripts/platform.sh start logs
sudo bash scripts/platform.sh start traces
sudo bash scripts/platform.sh verify
sudo bash scripts/platform.sh verify-datasources
sudo bash scripts/platform.sh test-telemetry
sudo bash scripts/platform.sh lock-images
```

Readiness checks use a short-lived Python diagnostic container inside the private Docker network. No shell or `curl` is assumed to exist inside the backend images. Prometheus includes the future full set of platform targets from the beginning, so targets for layers not started yet are expected to be down.

Use an SSH tunnel from your local computer to access Grafana:

```text
ssh -L 3000:127.0.0.1:3000 grafana@<MONITORING_PUBLIC_IP>
```

Open `http://localhost:3000`, sign in as `admin` using the securely stored password, and use Explore. The three datasources are provisioned; dashboards and alert rules are intentionally absent.

## Operations and current verification

See [Operations](docs/operations.md), [Capacity](docs/capacity.md), and [Verification record](docs/verification.md). Capacity is a test target, not a benchmark claim. Azure creation, host setup, and a real capacity test require the intended Azure subscription and VM access.

Normal shutdown uses `sudo bash scripts/platform.sh stop`. This preserves all telemetry. The helper contains no volume-deletion or disk-formatting operation.
