#!/usr/bin/env python3
"""Verify Grafana's provisioned datasource definitions and backend health."""
import argparse
import base64
import json
from pathlib import Path
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--grafana-url", default="http://grafana:3000")
    parser.add_argument("--password-file", default="/run/secrets/grafana_admin_password")
    args = parser.parse_args()
    try:
        password = Path(args.password_file).read_text().strip()
        authorization = "Basic " + base64.b64encode(("admin:" + password).encode()).decode()
        del password
        def get(path):
            request = Request(args.grafana_url.rstrip("/") + path,
                              headers={"Authorization": authorization, "Accept": "application/json"})
            with urlopen(request, timeout=15) as response:
                return json.load(response)
        for uid, endpoint in (("prometheus", "http://prometheus:9090"),
                              ("loki", "http://loki:3100"), ("tempo", "http://tempo:3200")):
            source = get("/api/datasources/uid/" + uid)
            if source.get("type") != uid or source.get("url") != endpoint:
                raise ValueError(f"{uid}: provisioned type or URL differs from the platform contract")
            health = get("/api/datasources/uid/" + uid + "/health")
            if health.get("status", "").upper() != "OK":
                raise ValueError(f"{uid}: Grafana's datasource health check failed")
            print(f"PASS Grafana datasource {uid}: definition and backend connection verified")
        return 0
    except HTTPError as error:
        error.close()
        print(f"FAIL Grafana datasource check: HTTP {error.code}; inspect credentials and backend logs", file=sys.stderr)
    except (URLError, OSError, ValueError) as error:
        print(f"FAIL Grafana datasource check: {type(error).__name__}; inspect credentials and backend configuration", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
