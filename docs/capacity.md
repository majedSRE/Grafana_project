# Capacity assumptions and validation

The Azure target is 8 vCPUs / 32 GiB RAM with a 512 GiB P20 SSD data disk. This is capacity for monitoring telemetry; the future application runs elsewhere.

| Signal | Initial envelope | Retention |
| --- | --- | --- |
| Metrics | 100,000 active series after histogram expansion, at 15-second intervals | 14 days, also capped at 60 GiB of retained TSDB blocks |
| Logs | 5 GiB/day of uncompressed input | 14 days |
| Traces | 100 received spans/second, averaging 1 KiB each | 14 days |
| Queries | A few concurrent routine Explore queries | Not a large analytical workload |

Prometheus' approximate 1-2 bytes/sample gives 7.5-15 GiB for the sample data alone at this rate. Indexes, WAL, changing labels, and compaction need additional space. Logs represent 70 GiB raw input over 14 days; traces represent approximately 115 GiB. Compression and storage metadata change the actual on-disk result. [Prometheus storage reference](https://prometheus.io/docs/prometheus/latest/storage/)

Plan 80 GiB for Prometheus, 120 GiB for Loki, 200 GiB for Tempo, 12 GiB for Grafana/Alloy state, and approximately 100 GiB for filesystem overhead and shared free space. These are budgets, not directory quotas. Loki retention does not provide disk-full eviction; inspect disk growth before running larger experiments.

Container memory limits total 25 GiB: Grafana 2, Prometheus 6, Loki 6, Tempo 8, Alloy 2, cAdvisor 1. The remaining 7 GiB supports Linux, Node Exporter, filesystem cache, and headroom. These limits are ceilings, not preallocated reservations or promised minimum requirements.

## Capacity acceptance run on Azure

Perform this only after the functional checks pass, using a separate load-generating client so its resource use does not distort the monitoring VM results. The ordinary telemetry smoke probe is **not** a capacity benchmark.

The standard-library generator supports the planned rates. On an authorized client in the VNet, inspect the target first with `--dry-run`, then run the same command without that flag:

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
