# Verification record

This document separates repository checks from deployment acceptance. A configuration file passing validation does not prove the Azure platform is deployed or sized correctly.

## Completed repository checks

- Terraform 1.15.6 formatting and AzureRM 5.5.0 schema validation pass. All seven mocked Terraform plan tests pass with container networking disabled: capacity/storage, networking/access, and rejection of open SSH, invalid IPv4, private-key input, unpinned images and invalid subscription IDs. These are not real Azure plans.
- Root Docker Compose model parses successfully with `.env.example`.
- All 27 offline tests pass, covering OTLP payloads, partial failures, exact log/metric/trace matching, target failures, saved-record verification, and capacity-generator contracts.
- Prometheus configuration passes the pinned Prometheus 3.14.0 `promtool` validator.
- Loki 3.7.7, Tempo 3.0.3, and Alloy 1.19.2 accept their configurations using their actual container validators.

## Completed local integration checks (2026-09-14)

Docker Desktop supplied 8 CPUs and approximately 7.7 GiB RAM. This is a functional smoke environment, not the 32 GiB Azure capacity target.

- Grafana, Prometheus, Loki, Tempo, Alloy, and cAdvisor readiness passed.
- All seven configured Prometheus jobs reported `up=1`; local Node Exporter used the documented containerized test substitute.
- Grafana's authenticated datasource API verified each provisioned definition and successfully checked all three backend connections.
- Synthetic cumulative metrics, a unique OTLP log, and a parent/child trace were accepted through Alloy and their actual contents were found in Prometheus, Loki, and Tempo.
- A unique Docker stdout marker was collected by Alloy and found in Loki under `source="docker"`.
- After a normal full-stack stop/start, the original metrics, log, and trace were still queryable without re-emission. No volumes were deleted.
- A five-second generator smoke run acknowledged 10 metric samples, 6 log records, and 10 spans. This is not the one-hour capacity acceptance run.
- `images.lock.yml` records the six tested image digests.
- Both Bash operating/firewall scripts pass syntax validation, and Git ignore rules exclude local secrets, environment files, Terraform state, and validation artifacts.
- The local validation stack was stopped after the final successful readiness check. Its containers and persistent test volumes are retained; no existing user images or data were removed.

The tests caught and corrected an Alloy gRPC exporter timeout setting placed in the wrong block, an HTTP client JSON content-negotiation omission, and a probe that incorrectly rejected empty `partialSuccess` acknowledgements. Actual rejected-record counts and warning messages still fail verification.

## Pending runtime and Azure acceptance

- Azure resource creation, actual host disk/firewall/systemd installation, and secure SSH access.
- Real host journal collection and the full host Prometheus target.
- Grafana datasource connectivity, all three OTLP paths, container logs, and restart persistence on Azure.
- VM reboot, private-port reachability, cold backup/restore, and the capacity acceptance run.

Update this record only with observed results. Keep per-run synthetic records under ignored `validation-results/`, not in Git.

## Repeat offline checks

For infrastructure, initialize the pinned provider once (requires internet), then run these from `terraform/`. Validation and mocked tests do not require Azure credentials:

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

## Isolated Docker Desktop smoke setup

`tests/compose.local.yml` merges with the deployment file but substitutes isolated named volumes, a generated test secret, a containerized Node Exporter, and loopback ports 13000/14317/14318. It uses the same backend/Alloy configurations. Its empty journal volume cannot validate an Ubuntu systemd journal, and Docker Desktop cannot validate Azure capacity or NSGs.

Do not run it alongside the Azure-style local project: both intentionally use the documented `172.28.0.0/24` bridge subnet to preserve the exact Prometheus target configuration.

```text
docker compose --env-file .env.example -f docker-compose.yml -f tests/compose.local.yml -p observability-validation up -d
```

Use a diagnostic container on the `observability-validation` network with this repository's `scripts` directory mounted at `/checks`, then run `/checks/verify.py --targets` and `/checks/telemetry_probe.py`. Finish with the matching Compose `stop` command. Keep validation volumes intact; normal verification never calls `down -v`.
