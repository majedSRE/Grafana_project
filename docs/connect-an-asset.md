# Connect an Asset to OctoSight

This is an optional integration guide. Use it only after OctoSight itself has
been deployed and validated through [Getting Started](getting-started.md).
OctoSight does not automatically support or discover every possible asset.
The asset's available telemetry interface, network path, and required
configuration determine the integration.

Not every asset needs metrics, logs, and traces. Select the signals that are
available and useful for the operational question.

## A. Linux host or VM metrics

Pattern:

```text
Linux host -> Node Exporter -> Prometheus
```

The source host must be able to reach the Prometheus scrape path, and the
monitoring host must be able to reach the exporter. Use placeholders for the
asset's private address and identity:

- `<WORKLOAD_PRIVATE_IP>`
- `<SERVER_NAME>`
- `<ENVIRONMENT>`

An explicit scrape job can be added to
`config/prometheus/prometheus.yml`:

```yaml
scrape_configs:
  - job_name: workload-linux
    static_configs:
      - targets: ["<WORKLOAD_PRIVATE_IP>:9100"]
        labels:
          server: "<SERVER_NAME>"
          environment: "<ENVIRONMENT>"
```

The target must be network-reachable and the exporter port must be restricted
to the required source. Review the change, then validate from
`/opt/observability`:

```bash
sudo bash scripts/platform.sh validate-config
sudo bash scripts/platform.sh start metrics
sudo bash scripts/platform.sh verify
```

These commands use the repository's validation/start procedure. Confirm the
new target in Prometheus before treating the integration as complete.

## B. Prometheus-compatible asset

Pattern:

```text
Asset or exporter -> Prometheus
```

A compatible `/metrics` endpoint still requires network reachability and an
explicit scrape configuration unless service discovery has separately been
configured. Add the reviewed job to
`config/prometheus/prometheus.yml`, validate it, start the metrics layer, and
verify the target as shown above. Do not assume that a reachable endpoint is
automatically scraped.

## C. Container or Docker environment

cAdvisor provides resource metrics for containers on the Docker host where
cAdvisor runs. It does not automatically monitor containers on a different
Docker host.

Alloy can collect appropriate Docker stdout logs when it has access to the
source host's Docker API and log path. A remote Docker host needs an Alloy
collector or another supported collection path on or near that host; the
monitoring VM's local Docker discovery is not a remote log agent.

## D. OpenTelemetry application or service

Pattern:

```text
Application or service -> OTLP -> Alloy
```

Use private connectivity and configure the client for one of the existing
receivers:

```text
OTLP/gRPC: <MONITORING_PRIVATE_IP>:4317
OTLP/HTTP: http://<MONITORING_PRIVATE_IP>:4318
```

For OTLP/HTTP, SDKs commonly use the signal-specific paths `/v1/metrics`,
`/v1/logs`, and `/v1/traces` when the SDK expects a base endpoint. Preserve
these resource attributes when they are available:

```text
service.name
service.namespace
deployment.environment.name
```

The current pipeline routes OTLP metrics to Prometheus and OTLP traces to
Tempo. OTLP logs are supported when the client and deployed pipeline are
configured for them; OTLP logs are not a requirement for every asset.

## E. Logs

Pattern:

```text
Asset -> an appropriate collector/Alloy path -> Loki
```

Remote logs require a collection agent or path on or near the source. The
monitoring VM's Alloy Docker discovery collects local Docker logs, not remote
containers. Choose one intentional ingestion path for each stream; do not
send the same stream through Docker collection and OTLP unless duplication is
deliberate.

When the source supports structured logs, preserve service identity and trace
correlation metadata where possible. Verify the resulting labels and log
content in Loki and Grafana Explore rather than assuming a label mapping.

## F. Network and security requirements

- Prefer private or otherwise restricted telemetry connectivity.
- Permit only the required source hosts/networks and ports.
- Do not broadly expose telemetry or backend ports to the public internet.
- Treat backend user access and telemetry ingestion as separate paths.
- Do not assume every NSG rule is automatically Terraform-managed; inspect the
  applicable infrastructure and organization controls.
- Keep user credentials, webhook values, and private keys outside the
  repository.

## G. Verification

Verify only the signal types the asset supplies:

1. Prometheus: query the new target or metric and confirm recent samples.
2. Loki: query the source's resulting labels and confirm a recent log entry.
3. Tempo: search by the preserved `service.name` and confirm recent traces.
4. Grafana: confirm the applicable dashboard panels or Explore queries show
   the same data.

If a signal is absent, check source configuration, network reachability,
collector logs, and the backend's ingestion path in that order. Do not infer
that an asset is unhealthy solely because it does not provide a signal it was
never configured to emit.
