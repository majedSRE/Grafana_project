#!/usr/bin/env python3
"""Read-only platform readiness checks, normally run on its Docker network."""

from __future__ import annotations

import argparse
import json
import re
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen


MAX_RESPONSE_BYTES = 4 * 1024 * 1024
DEFAULT_ENDPOINTS = {
    "grafana": "http://grafana:3000",
    "prometheus": "http://prometheus:9090",
    "loki": "http://loki:3100",
    "tempo": "http://tempo:3200",
    "alloy": "http://alloy:12345",
    "cadvisor": "http://cadvisor:8080",
}
DEFAULT_JOBS = [
    "linux-server", "cadvisor", "prometheus", "grafana", "loki", "tempo", "alloy"
]


class ProbeError(Exception):
    """An actionable verification failure without credentials or response dumps."""


def positive_float(value: str) -> float:
    try:
        number = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be a positive number") from error
    if not 0 < number < float("inf"):
        raise argparse.ArgumentTypeError("must be a finite positive number")
    return number


def request_text(url: str, timeout: float = 5, payload: dict | None = None, *, accept: str = "*/*") -> str:
    """Fetch bounded HTTP content; do not echo potentially sensitive bodies/URLs."""
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ProbeError("endpoint must be an absolute HTTP or HTTPS URL")
    if parsed.username or parsed.password:
        raise ProbeError("embedded endpoint credentials are not supported")
    headers = {"Accept": "application/json" if payload is not None else accept}
    encoded = None
    if payload is not None:
        encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = Request(url, data=encoded, headers=headers)
    try:
        with urlopen(request, timeout=timeout) as response:
            if response.status != 200:
                raise ProbeError(f"HTTP {response.status}; expected HTTP 200")
            content = response.read(MAX_RESPONSE_BYTES + 1)
    except HTTPError as error:
        error.close()
        raise ProbeError(f"HTTP {error.code}; check the component logs and endpoint") from error
    except (URLError, TimeoutError, OSError) as error:
        raise ProbeError("connection failed or timed out; check service state, DNS, and networking") from error
    if len(content) > MAX_RESPONSE_BYTES:
        raise ProbeError("response exceeded the 4 MiB safety limit")
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ProbeError("endpoint did not return UTF-8 text") from error


def request_json(url: str, timeout: float = 5, payload: dict | None = None) -> dict:
    try:
        result = json.loads(request_text(url, timeout, payload, accept="application/json"))
    except json.JSONDecodeError as error:
        raise ProbeError("endpoint did not return valid JSON") from error
    if not isinstance(result, dict):
        raise ProbeError("endpoint did not return a JSON object")
    return result


def query_results(response: dict, result_type: str) -> list:
    if response.get("status") != "success":
        raise ProbeError("query API reported failure; check query configuration and backend logs")
    data = response.get("data")
    if not isinstance(data, dict) or data.get("resultType") != result_type:
        raise ProbeError(f"query API did not return a {result_type} result")
    results = data.get("result")
    if not isinstance(results, list):
        raise ProbeError("query API result is malformed")
    return results


def check_service(service: str, endpoint: str, timeout: float) -> None:
    base = endpoint.rstrip("/")
    if service == "grafana":
        if request_json(base + "/api/health", timeout).get("database") != "ok":
            raise ProbeError("Grafana database health is not ok")
        return
    if service == "cadvisor":
        body = request_text(base + "/metrics", timeout)
        for metric in ("container_cpu_usage_seconds_total", "container_memory_working_set_bytes"):
            if not re.search(r"^" + metric + r"(?:\{|\s)", body, re.MULTILINE):
                raise ProbeError(f"cAdvisor is reachable but {metric} samples are missing")
        return
    checks = {
        "prometheus": [("/-/ready", "Prometheus Server is Ready.")],
        "loki": [("/ready", "ready")],
        "tempo": [("/ready", "ready")],
        "alloy": [
            ("/-/ready", "Alloy is ready."),
            ("/-/healthy", "All Alloy components are healthy."),
        ],
    }
    for path, expected in checks[service]:
        if request_text(base + path, timeout).strip() != expected:
            raise ProbeError(f"unexpected readiness response from {path}; inspect component state")


def check_targets(endpoint: str, expected_jobs: list[str], timeout: float) -> None:
    response = request_json(
        endpoint.rstrip("/") + "/api/v1/query?" + urlencode({"query": "up"}), timeout
    )
    results = query_results(response, "vector")
    seen = set()
    down = []
    for result in results:
        if not isinstance(result, dict) or not isinstance(result.get("metric"), dict):
            raise ProbeError("Prometheus up response contains a malformed series")
        labels = result["metric"]
        job = labels.get("job", "")
        value = result.get("value")
        try:
            healthy = isinstance(value, list) and len(value) == 2 and float(value[1]) == 1
        except (ValueError, TypeError):
            healthy = False
        seen.add(job)
        if not healthy:
            down.append(job or "unlabelled target")
    missing = sorted(set(expected_jobs) - seen)
    problems = []
    if missing:
        problems.append("missing jobs: " + ", ".join(missing))
    if down:
        problems.append("targets not up: " + ", ".join(sorted(set(down))))
    if problems:
        raise ProbeError("; ".join(problems) + "; inspect Prometheus Targets and exporter logs")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--services", nargs="+", choices=DEFAULT_ENDPOINTS, default=list(DEFAULT_ENDPOINTS))
    parser.add_argument("--targets", action="store_true", help="also require expected scrape jobs and up=1")
    parser.add_argument("--expected-jobs", nargs="+", default=DEFAULT_JOBS)
    parser.add_argument("--timeout", type=positive_float, default=5, help="per-request timeout in seconds")
    for service, endpoint in DEFAULT_ENDPOINTS.items():
        parser.add_argument(f"--{service}-url", default=endpoint)
    args = parser.parse_args(argv)
    failures = 0
    for service in args.services:
        try:
            check_service(service, getattr(args, service + "_url"), args.timeout)
            print(f"PASS {service}: readiness and response content verified")
        except ProbeError as error:
            failures += 1
            print(f"FAIL {service}: {error}", file=sys.stderr)
    if args.targets:
        try:
            check_targets(args.prometheus_url, args.expected_jobs, args.timeout)
            print("PASS prometheus: expected scrape jobs present and every returned target is up")
        except ProbeError as error:
            failures += 1
            print(f"FAIL prometheus targets: {error}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
