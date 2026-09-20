# Capacity assumptions and validation

The Azure target is 4 vCPUs / 32 GiB RAM with a 256 GiB P15 Premium SSD data disk. This is sized for the short-lived, low-ingestion capstone/demo observability workload; the application runs separately. It is not a production-scale capacity claim.

| Signal | Initial envelope | Retention |
| --- | --- | --- |
| Metrics | Low-rate application and infrastructure telemetry for the demo | 14 days, also capped at 60 GiB of retained TSDB blocks |
| Logs | Normal application, container, and system logs from the demo | 14 days |
| Traces | Normal interactive microservice traces from the demo | 14 days |
| Queries | A few concurrent routine Explore queries | Not a large analytical workload |

The 256 GiB disk is appropriate for the short, low-ingestion demo while retaining the existing 14-day backend policies. Actual usage depends on query history, WALs, indexes, compaction, and log/trace volume; monitor free space during the demonstration. [Prometheus storage reference](https://prometheus.io/docs/prometheus/latest/storage/)

These are operational headroom assumptions, not directory quotas or proof of production capacity. Loki retention does not provide disk-full eviction; inspect disk growth before running larger experiments.

Container memory limits total 25 GiB: Grafana 2, Prometheus 6, Loki 6, Tempo 8, Alloy 2, cAdvisor 1. The remaining 7 GiB supports Linux, Node Exporter, filesystem cache, and headroom. These limits are ceilings, not preallocated reservations or promised minimum requirements.

## Deferred heavy synthetic scenario

The previous heavy synthetic scenario is retained as a reference for future engineering work only. It is **not** an acceptance test for this short-lived demo VM, and the final 256 GiB design must not be presented as proving that workload. The ordinary telemetry smoke probe is also not a capacity benchmark.

If this scenario is ever revisited on a separately approved, appropriately sized test environment, the standard-library generator supports the former planned rates. Inspect the target first with `--dry-run`; do not run it against the final capstone VM as an acceptance claim:

```bash
python3 scripts/capacity_load.py \
  --otlp-url http://10.20.0.4:4318 \
  --duration 3600 --series 100000 --metric-interval 15 \
  --spans-per-second 100 --logs-gib-per-day 5 \
  --burst-at 1800 --burst-seconds 300 --burst-factor 2 --dry-run
```

Log rates count 1 KiB bodies; OTLP metadata adds overhead. Spans have randomized attributes and roughly 1 KiB JSON representations. Measure backend bytes rather than assuming wire and stored sizes match. The generator reports acknowledged counts and fails on export errors or client scheduling lag. These counts alone cannot prove durable delivery. Synthetic data remains subject to normal retention; it is never deleted automatically by the test.

1. Record VM SKU, allocated RAM, disk type/performance, image digests, and the test start time.
2. Generate 100,000 stable Prometheus series at 15-second intervals, 5 GiB/day-equivalent logs (about 62 KiB/second), and 100 spans/second with representative attributes. Keep series/labels stable rather than creating new ones on every request.
3. Maintain the baseline for at least one hour, with a five-minute 2x ingestion burst. Twice the sample frequency during the burst increases metrics ingestion without doubling active series.
4. Perform representative queries during ingestion. Inspect CPU, available RAM, component restarts, backend errors, and Alloy's accepted/exported/rejected signal counters.
5. Require average CPU below 70%, available host RAM above 20%, no out-of-memory terminations or unexplained lost/rejected telemetry, and a backlog that clears after the burst.
6. Measure disk growth after compaction has run. Extrapolate 14-day storage, including WAL/index overhead, and require projected usage below 80%. One short run is only a preliminary estimate; repeat with the real workload and longer history before relying on the retention claim.

Sampling is not enabled by default. If future incoming telemetry exceeds this envelope, stop increasing load, measure the bottleneck, and decide whether to resize resources, tune collection, or introduce sampling. Do not silently discard telemetry to make a capacity test pass.

A single VM is a shared failure domain. High availability and production object storage remain future design work.
