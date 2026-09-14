#!/usr/bin/env python3
"""Emit synthetic OTLP signals and verify their contents in all three backends.

No application or SDK dependency is needed. Use --record with --verify-only to
verify that the same signals remain queryable after a normal platform restart.
OTLP JSON uses hexadecimal trace/span IDs and integer enums, per the OTLP spec.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import json
from pathlib import Path
import re
import secrets
import sys
import time
from urllib.parse import urlencode

from verify import ProbeError, positive_float, query_results, request_json


SERVICE = "platform-validation"
SPAN_NAMES = ("platform-validation-root", "platform-validation-child")
COUNTER_NAME = "platform_validation_counter_total"
GAUGE_NAME = "platform_validation_gauge"


def attribute(key: str, value: str) -> dict:
    return {"key": key, "value": {"stringValue": value}}


def new_run() -> dict:
    run_id = secrets.token_hex(16)
    return {
        "schemaVersion": 1,
        "runId": run_id,
        "createdUnixNano": str(time.time_ns()),
        "traceId": secrets.token_hex(16),
        "rootSpanId": secrets.token_hex(8),
        "childSpanId": secrets.token_hex(8),
        "metricValue": int(run_id[:12], 16) + 1,
        "logMarker": "PLATFORM_OTLP_TEST_" + run_id,
    }


def validate_run(run: dict) -> dict:
    if not isinstance(run, dict) or run.get("schemaVersion") != 1:
        raise ProbeError("record has an unsupported schemaVersion")
    for field, length in (("runId", 32), ("traceId", 32), ("rootSpanId", 16), ("childSpanId", 16)):
        value = run.get(field)
        if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{" + str(length) + "}", value):
            raise ProbeError(f"record has an invalid {field}")
    value = run.get("metricValue")
    if isinstance(value, bool) or not isinstance(value, int) or not 0 < value < 2**53:
        raise ProbeError("record has an invalid metricValue")
    timestamp = run.get("createdUnixNano")
    if not isinstance(timestamp, str) or not timestamp.isdecimal() or int(timestamp) <= 1_000_000_000:
        raise ProbeError("record has an invalid createdUnixNano")
    if run.get("logMarker") != "PLATFORM_OTLP_TEST_" + run["runId"]:
        raise ProbeError("record has an invalid logMarker")
    return run


def payloads(run: dict) -> dict[str, dict]:
    validate_run(run)
    timestamp = int(run["createdUnixNano"])
    resource = {"attributes": [
        attribute("service.name", SERVICE),
        attribute("deployment.environment.name", "test"),
    ]}
    scope = {"name": "observability-platform-validation", "version": "1.0"}
    point = {
        "attributes": [attribute("validation.kind", "smoke")],
        "startTimeUnixNano": str(timestamp - 1_000_000_000),
        "timeUnixNano": str(timestamp),
        "asInt": str(run["metricValue"]),
    }
    metrics = {"resourceMetrics": [{"resource": resource, "scopeMetrics": [{
        "scope": scope,
        "metrics": [
            {"name": "platform_validation_counter", "sum": {
                "aggregationTemporality": 2, "isMonotonic": True, "dataPoints": [point],
            }},
            {"name": GAUGE_NAME, "gauge": {"dataPoints": [point]}},
        ],
    }]}]}
    logs = {"resourceLogs": [{"resource": resource, "scopeLogs": [{
        "scope": scope,
        "logRecords": [{
            "timeUnixNano": str(timestamp), "observedTimeUnixNano": str(timestamp),
            "severityNumber": 9, "severityText": "INFO",
            "body": {"stringValue": run["logMarker"]},
            "attributes": [attribute("validation.run_id", run["runId"])],
            "traceId": run["traceId"], "spanId": run["rootSpanId"], "flags": 1,
        }],
    }]}]}
    spans = []
    for index, name in enumerate(SPAN_NAMES):
        span = {
            "traceId": run["traceId"],
            "spanId": run["rootSpanId"] if index == 0 else run["childSpanId"],
            "name": name, "kind": 2 if index == 0 else 1,
            "startTimeUnixNano": str(timestamp - 100_000_000 + index * 10_000_000),
            "endTimeUnixNano": str(timestamp - index * 10_000_000),
            "attributes": [attribute("validation.run_id", run["runId"])],
            "status": {"code": 1}, "flags": 1,
        }
        if index:
            span["parentSpanId"] = run["rootSpanId"]
        spans.append(span)
    traces = {"resourceSpans": [{"resource": resource, "scopeSpans": [{"scope": scope, "spans": spans}]}]}
    return {"metrics": metrics, "logs": logs, "traces": traces}


def require_otlp_success(response: dict) -> None:
    # HTTP 200 can still mean rejected records or a pipeline warning. Do not retry
    # an ambiguous export: doing so can duplicate accepted logs and spans.
    partial = response.get("partialSuccess", {})
    if not isinstance(partial, dict):
        raise ProbeError("OTLP returned a malformed partialSuccess acknowledgement")
    try:
        rejected = any(int(partial.get(key, 0)) != 0 for key in (
            "rejectedSpans", "rejectedDataPoints", "rejectedLogRecords"
        ))
    except (TypeError, ValueError) as error:
        raise ProbeError("OTLP returned malformed rejected-record counts") from error
    if rejected or partial.get("errorMessage"):
        raise ProbeError("OTLP rejected records or returned a warning; inspect Alloy logs before sending more telemetry")
    if "code" in response or "message" in response:
        raise ProbeError("OTLP returned an error envelope despite HTTP 200")


def emit(run: dict, endpoint: str, timeout: float) -> None:
    for signal, payload in payloads(run).items():
        response = request_json(endpoint.rstrip("/") + "/v1/" + signal, timeout, payload)
        require_otlp_success(response)
        print(f"PASS emit {signal}: Alloy accepted the OTLP request", flush=True)


def check_metrics(run: dict, endpoint: str, timeout: float) -> None:
    # Query at the original sample time, so restart verification also works once
    # the default instant-vector lookback has elapsed. Unique values avoid adding
    # a run ID label (and hence new time series) to each probe execution.
    query_time = f"{int(run['createdUnixNano']) / 1_000_000_000 + 0.01:.3f}"
    for metric in (COUNTER_NAME, GAUGE_NAME):
        query = metric + '{job="' + SERVICE + '",validation_kind="smoke"}'
        response = request_json(endpoint.rstrip("/") + "/api/v1/query?" + urlencode({
            "query": query, "time": query_time,
        }), timeout)
        found = False
        for result in query_results(response, "vector"):
            try:
                found = found or float(result["value"][1]) == run["metricValue"]
            except (KeyError, IndexError, ValueError, TypeError):
                continue
        if not found:
            raise ProbeError(f"{metric}: matching sample not yet queryable; inspect Alloy metrics export")


def check_logs(run: dict, endpoint: str, timeout: float) -> None:
    timestamp = int(run["createdUnixNano"])
    query = '{service_name="' + SERVICE + '"} |= ' + json.dumps(run["logMarker"])
    response = request_json(endpoint.rstrip("/") + "/loki/api/v1/query_range?" + urlencode({
        "query": query, "start": str(timestamp - 60_000_000_000),
        "end": str(timestamp + 60_000_000_000), "limit": "100",
    }), timeout)
    for result in query_results(response, "streams"):
        if not isinstance(result, dict):
            continue
        for value in result.get("values", []):
            if isinstance(value, list) and len(value) >= 2 and run["logMarker"] in str(value[1]):
                return
    raise ProbeError("unique log marker not yet queryable; inspect Alloy logs export and Loki ingestion")


def identifier_hex(value: str) -> str:
    # Tempo's mostly-OTLP JSON API may serialize bytes as base64, whereas OTLP
    # ingestion requires hex. Accept either representation for query responses.
    if isinstance(value, str) and re.fullmatch(r"(?:[0-9a-fA-F]{16}|[0-9a-fA-F]{32})", value):
        return value.lower()
    try:
        return base64.b64decode(value, validate=True).hex()
    except (binascii.Error, ValueError, TypeError):
        return ""


def returned_spans(node: object):
    if isinstance(node, dict):
        spans = node.get("spans")
        if isinstance(spans, list):
            yield from (span for span in spans if isinstance(span, dict))
        for key, child in node.items():
            if key != "spans":
                yield from returned_spans(child)
    elif isinstance(node, list):
        for child in node:
            yield from returned_spans(child)


def check_trace(run: dict, endpoint: str, timeout: float) -> None:
    timestamp = int(run["createdUnixNano"]) // 1_000_000_000
    response = request_json(endpoint.rstrip("/") + "/api/traces/" + run["traceId"] + "?" + urlencode({
        "start": timestamp - 60, "end": timestamp + 60,
    }), timeout)
    found = {}
    for span in returned_spans(response):
        if identifier_hex(span.get("traceId", "")) == run["traceId"]:
            found[identifier_hex(span.get("spanId", ""))] = span
    root = found.get(run["rootSpanId"], {})
    child = found.get(run["childSpanId"], {})
    if (root.get("name") != SPAN_NAMES[0] or child.get("name") != SPAN_NAMES[1]
            or identifier_hex(child.get("parentSpanId", "")) != run["rootSpanId"]):
        raise ProbeError("complete parent/child trace not yet queryable; inspect Alloy traces export and Tempo")


def verify_signals(run: dict, args: argparse.Namespace) -> None:
    pending = {"metrics": (check_metrics, args.prometheus_url), "logs": (check_logs, args.loki_url),
               "traces": (check_trace, args.tempo_url)}
    deadline = time.monotonic() + args.wait
    errors = {}
    while pending:
        for signal, (check, endpoint) in list(pending.items()):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                check(run, endpoint, min(args.timeout, remaining))
                print(f"PASS query {signal}: emitted content found in backend", flush=True)
                del pending[signal]
            except ProbeError as error:
                errors[signal] = str(error)
        if pending:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                details = "; ".join(signal + ": " + errors.get(signal, "deadline reached") for signal in pending)
                raise ProbeError("verification deadline reached; " + details)
            time.sleep(min(2, remaining))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--emit-only", action="store_true")
    mode.add_argument("--verify-only", action="store_true")
    parser.add_argument("--record", type=Path, help="JSON run record; written on emission, read with --verify-only")
    parser.add_argument("--otlp-url", default="http://alloy:4318")
    parser.add_argument("--prometheus-url", default="http://prometheus:9090")
    parser.add_argument("--loki-url", default="http://loki:3100")
    parser.add_argument("--tempo-url", default="http://tempo:3200")
    parser.add_argument("--timeout", type=positive_float, default=5, help="per-request timeout in seconds")
    parser.add_argument("--wait", type=positive_float, default=90, help="bounded backend verification window in seconds")
    args = parser.parse_args(argv)
    if args.verify_only and not args.record:
        parser.error("--verify-only requires --record")
    try:
        if args.verify_only:
            run = validate_run(json.loads(args.record.read_text(encoding="utf-8")))
        else:
            run = new_run()
            if args.record:
                # Refuse to overwrite earlier evidence accidentally.
                with args.record.open("x", encoding="utf-8") as record:
                    json.dump(run, record, indent=2)
                    record.write("\n")
            print("RUN " + json.dumps(run, sort_keys=True), flush=True)
            emit(run, args.otlp_url, args.timeout)
        if not args.emit_only:
            verify_signals(run, args)
        return 0
    except (ProbeError, OSError, json.JSONDecodeError) as error:
        print(f"FAIL telemetry probe: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
