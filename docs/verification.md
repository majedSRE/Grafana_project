# Verification record

This document is evidence, not the deployment guide. A repository check does
not by itself prove that Azure infrastructure or a live platform exists.

## A. Historical repository and local validation

The following results are retained as historical evidence:

- Terraform 1.15.6 formatting and AzureRM 5.5.0 schema validation passed in
  the recorded validation run. Seven mocked Terraform plan tests passed with
  container networking disabled. These are not real Azure plans.
- The root Docker Compose model parsed successfully with the documented
  environment inputs.
- The recorded offline run executed 27 tests; 26 passed locally. One test,
  `test_emit_record_can_be_verified_without_resending`, could not complete on
  the Windows workstation because temporary-directory cleanup encountered a
  Windows permission error. It must be rerun on Ubuntu or GitHub Actions before
  final acceptance.
- Prometheus, Alertmanager, Loki, Tempo, and Alloy configuration validation
  passed in the recorded environment using their pinned service validators.
- The five provisioned dashboard JSON files parsed successfully.

## B. Historical platform smoke and telemetry evidence

The local integration evidence below was captured on 2026-09-19. Docker
Desktop supplied 8 CPUs and approximately 7.7 GiB RAM, so it is functional
smoke evidence and not evidence for the Azure capacity target.

- Grafana, Prometheus, Loki, Tempo, Alloy, cAdvisor, and Alertmanager
  readiness passed.
- All seven configured Prometheus jobs reported `up=1`; local Node Exporter
  used the documented containerized test substitute.
- Grafana's authenticated datasource API verified the provisioned Prometheus,
  Loki, and Tempo definitions and backend connections.
- Synthetic cumulative metrics, a unique OTLP log, and a parent/child trace
  were accepted through Alloy and found in Prometheus, Loki, and Tempo.
- A unique Docker stdout marker was collected by Alloy and found in Loki under
  `source="docker"`.
- After a normal full-stack stop/start, the original metrics, log, and trace
  remained queryable without re-emission. No volumes were deleted.
- The recorded five-second generator acknowledged 10 metric samples, 6 log
  records, and 10 spans. This is not the one-hour capacity acceptance run.
- `images.lock.yml` records the tested platform image digests.
- Both Bash operating/firewall scripts passed syntax validation, and Git ignore
  rules excluded local secrets, environment files, Terraform state, and
  validation artifacts.

## C. Historical Azure validation evidence

The OctoSight monitoring environment was deployed in Central US with
monitoring VNet `10.20.0.0/16` and monitoring VM private IP `10.20.0.4`. The
VM was `Standard_E4as_v7` with a 64 GiB Standard SSD OS disk and a 256 GiB
Premium SSD telemetry disk.

An independent validation workload ran in South Central US with application
VNet `10.30.0.0/16` and application VM private IP `10.30.0.4`; VNet peering
provided the private telemetry path. This workload was TEST/VALIDATION
EVIDENCE ONLY. It is not part of OctoSight and is not required to reproduce
the platform.

Observed Azure validation included:

- OctoSight platform services, Node Exporter, cAdvisor, Prometheus target
  health, and Grafana datasource/dashboard checks.
- Application metrics, application logs in Loki, traces in Tempo, and
  trace-derived metrics in Prometheus.
- Alertmanager operation.
- A controlled infrastructure failure in which the validation workload VM
  became unavailable, its target went down, `MonitoringTargetDown` progressed
  from Pending to Firing, Alertmanager received the alert, and recovery
  cleared it.
- A controlled checkout failure that produced HTTP 500, failed transaction
  telemetry, and `ApplicationTransactionFailures` for the affected validation
  service; Alertmanager received it.
- Both FIRING and RESOLVED notifications in the configured `#octosight-alerts`
  Slack channel. The webhook value is not recorded here.

These observations validate the independent workload integration pattern; they
do not make that workload a platform dependency.

## D. Current Azure state and pending acceptance

Both Azure VMs are intentionally deallocated for cost control. Therefore this
document does not claim that OctoSight services are currently running or
healthy.

The following live acceptance checks remain pending when the VM is next
started:

- host disk mount, firewall, persistent journal, and Node Exporter validation
- real host and container Prometheus target health
- Grafana datasource connectivity and configured OTLP paths
- container log collection and restart persistence
- VM reboot behavior, private-port reachability, cold backup/restore, and the
  capacity acceptance run
- a fresh validation of the current application alert-rule revision

Update this record only with observed results from the applicable run.

## Repeat offline checks

From `terraform/`:

```text
terraform init -backend=false
terraform fmt -check -recursive
terraform validate
terraform test
```

From the repository root:

```text
docker compose --env-file .env.example config --quiet
python -m unittest discover -s tests -v
```

If a tool is unavailable, report that check as pending rather than treating it
as a configuration failure.

## Isolated Docker Desktop smoke setup

`tests/compose.local.yml` merges with the deployment file but substitutes
isolated named volumes, a generated test secret, a containerized Node Exporter,
and loopback ports 13000/14317/14318. Its empty journal volume cannot validate
an Ubuntu systemd journal, and Docker Desktop cannot validate Azure capacity
or NSGs.

Do not run it alongside the Azure-style local project: both intentionally use
the documented `172.28.0.0/24` bridge subnet to preserve the exact Prometheus
target configuration.

```text
docker compose --env-file .env.example -f docker-compose.yml -f images.lock.yml -f tests/compose.local.yml -p observability-validation up -d
```

Use a diagnostic container on the `observability-validation` network with the
repository's `scripts` directory mounted at `/checks`, then run
`/checks/verify.py --targets` and `/checks/telemetry_probe.py`. Finish with the
matching Compose `stop` command. Keep validation volumes intact; normal
verification never calls `down -v`.
