#!/usr/bin/env python3
"""Generate bounded synthetic OTLP load from a separate test client.

Counts report Alloy acknowledgements, not durable backend delivery. Evaluate
backend/host measurements separately using docs/capacity.md. No application SDK
or external Python package is needed. Default settings are a small smoke load.
"""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import json
import math
import secrets
import sys
import threading
import time

from telemetry_probe import attribute, require_otlp_success
from verify import ProbeError, positive_float, request_json


RESOURCE = {"attributes": [attribute("service.name", "platform-capacity"),
                           attribute("deployment.environment.name", "test")]}


def metric_payload(start, count, timestamp, start_timestamp):
    points = [{"attributes": [attribute("series", str(index))],
               "timeUnixNano": str(timestamp), "startTimeUnixNano": str(start_timestamp),
               "asDouble": float(index % 100)} for index in range(start, start + count)]
    return {"resourceMetrics": [{"resource": RESOURCE, "scopeMetrics": [{"metrics": [
        {"name": "platform_capacity_gauge", "gauge": {"dataPoints": points}}
    ]}]}]}


def log_payload(count, timestamp):
    records = [{"timeUnixNano": str(timestamp + index), "severityNumber": 9,
                "body": {"stringValue": secrets.token_urlsafe(768)}} for index in range(count)]
    return {"resourceLogs": [{"resource": RESOURCE, "scopeLogs": [{"logRecords": records}]}]}


def trace_payload(count, timestamp):
    spans = []
    for _ in range(count):
        spans.append({"traceId": secrets.token_hex(16), "spanId": secrets.token_hex(8),
                      "name": "capacity-operation", "kind": 1,
                      "startTimeUnixNano": str(timestamp - 1_000_000),
                      "endTimeUnixNano": str(timestamp), "status": {"code": 1},
                      "attributes": [attribute("synthetic.payload", secrets.token_urlsafe(480))]})
    return {"resourceSpans": [{"resource": RESOURCE, "scopeSpans": [{"spans": spans}]}]}


def burst_factor(elapsed, args):
    return args.burst_factor if args.burst_at <= elapsed < args.burst_at + args.burst_seconds else 1.0


def positive_int(value):
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return number


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--otlp-url", default="http://alloy:4318")
    parser.add_argument("--duration", type=positive_float, default=60)
    parser.add_argument("--series", type=positive_int, default=1000)
    parser.add_argument("--metric-interval", type=positive_float, default=15)
    parser.add_argument("--spans-per-second", type=positive_int, default=10)
    parser.add_argument("--logs-gib-per-day", type=positive_float, default=0.1)
    parser.add_argument("--burst-at", type=positive_float, default=1800)
    parser.add_argument("--burst-seconds", type=positive_float, default=300)
    parser.add_argument("--burst-factor", type=positive_float, default=2)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    target = {"duration_seconds": args.duration, "active_metric_series": args.series,
              "baseline_samples_per_second": args.series / args.metric_interval,
              "baseline_spans_per_second": args.spans_per_second,
              "baseline_log_body_bytes_per_second": args.logs_gib_per_day * 1024**3 / 86400,
              "burst_at_seconds": args.burst_at, "burst_duration_seconds": args.burst_seconds,
              "burst_factor": args.burst_factor}
    print("TARGET " + json.dumps(target), flush=True)
    if args.dry_run:
        return 0
    print("Synthetic data is retained by the backends. This reports acknowledged sends, not acceptance-test success.", flush=True)
    started = time.monotonic()
    start_timestamp = time.time_ns()
    stop = threading.Event()
    lock = threading.Lock()
    counts = Counter()

    def send(signal, payload, count):
        response = request_json(args.otlp_url.rstrip("/") + "/v1/" + signal, 10, payload)
        require_otlp_success(response)
        with lock:
            counts[signal] += count

    def worker(signal):
        next_tick = started
        fractional = 0.0
        while not stop.is_set():
            now = time.monotonic()
            elapsed = now - started
            if elapsed >= args.duration:
                return
            if now < next_tick:
                stop.wait(min(next_tick - now, args.duration - elapsed))
                continue
            factor = burst_factor(elapsed, args)
            timestamp = time.time_ns()
            try:
                if signal == "metrics":
                    interval = args.metric_interval / factor
                    for offset in range(0, args.series, 1000):
                        if stop.is_set():
                            return
                        count = min(1000, args.series - offset)
                        send(signal, metric_payload(offset, count, timestamp, start_timestamp), count)
                else:
                    interval = 1.0
                    rate = args.spans_per_second if signal == "traces" else target["baseline_log_body_bytes_per_second"] / 1024
                    fractional += rate * factor
                    count = math.floor(fractional)
                    fractional -= count
                    factory = trace_payload if signal == "traces" else log_payload
                    for offset in range(0, count, 500):
                        chunk = min(500, count - offset)
                        send(signal, factory(chunk, timestamp + offset), chunk)
                next_tick += interval
                if time.monotonic() - next_tick > interval:
                    raise ProbeError(f"{signal}: client cannot maintain target rate; do not count this as a capacity pass")
            except Exception:
                stop.set()
                raise

    try:
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = [pool.submit(worker, signal) for signal in ("metrics", "logs", "traces")]
            try:
                while not all(future.done() for future in futures):
                    stop.wait(5)
                    for future in futures:
                        if future.done() and future.exception():
                            raise future.exception()
                for future in futures:
                    future.result()
            except BaseException:
                # Signal workers before the executor waits for their shutdown,
                # including when the operator presses Ctrl+C during a long run.
                stop.set()
                raise
    except (ProbeError, OSError) as error:
        print("FAIL load generation: " + str(error), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        stop.set()
        print("Load interrupted; previously sent synthetic data is retained.", file=sys.stderr)
        return 130
    finally:
        print("ACKNOWLEDGED " + json.dumps(dict(counts, elapsed_seconds=round(time.monotonic() - started, 2))), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
