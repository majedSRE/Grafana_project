#!/usr/bin/env python3
"""Find a specific journal or Docker log marker through Loki, with a deadline."""
import argparse
import json
import sys
import time
from urllib.parse import urlencode
from verify import ProbeError, positive_float, query_results, request_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", choices=("docker", "systemd-journal"), required=True)
    parser.add_argument("--marker", required=True)
    parser.add_argument("--server", default="vm-monitoring-v2-test")
    parser.add_argument("--loki-url", default="http://loki:3100")
    parser.add_argument("--wait", type=positive_float, default=60)
    args = parser.parse_args()
    selector = '{server=' + json.dumps(args.server) + ',source=' + json.dumps(args.source) + '}'
    query = selector + ' |= ' + json.dumps(args.marker)
    start = time.time_ns() - 300_000_000_000
    deadline = time.monotonic() + args.wait
    while time.monotonic() < deadline:
        try:
            response = request_json(args.loki_url.rstrip("/") + "/loki/api/v1/query_range?" + urlencode({
                "query": query, "start": str(start), "end": str(time.time_ns()), "limit": "100",
            }), min(5, max(0.1, deadline - time.monotonic())))
            for stream in query_results(response, "streams"):
                if any(args.marker in str(value[1]) for value in stream.get("values", []) if len(value) >= 2):
                    print(f"PASS {args.source}: exact marker received by Loki")
                    return 0
        except ProbeError:
            pass
        time.sleep(min(2, max(0, deadline - time.monotonic())))
    print(f"FAIL {args.source}: marker not found; inspect journal/Docker discovery, permissions, and Alloy logs", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
