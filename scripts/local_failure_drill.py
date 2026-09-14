#!/usr/bin/env python3
"""Run a bounded, local-only restart drill against one Compose service."""

from __future__ import annotations

import argparse
import subprocess
import time


PROJECT = "observability-validation"
FILES = ("docker-compose.yml", "images.lock.yml", "tests/compose.local.yml")
SERVICES = ("prometheus", "grafana", "loki", "tempo", "alloy", "cadvisor", "alertmanager")


def compose(*args: str) -> None:
    command = ["docker", "compose", "--env-file", ".env.example"]
    for compose_file in FILES:
        command.extend(("-f", compose_file))
    command.extend(("-p", PROJECT, *args))
    subprocess.run(command, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("service", choices=SERVICES)
    parser.add_argument("--seconds", type=int, default=10)
    args = parser.parse_args()
    if args.seconds < 1 or args.seconds > 120:
        parser.error("--seconds must be between 1 and 120")

    print(f"Stopping local service {args.service} for {args.seconds}s", flush=True)
    compose("stop", args.service)
    time.sleep(args.seconds)
    compose("start", args.service)
    compose("ps", args.service)
    print(f"PASS restart drill completed for {args.service}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
