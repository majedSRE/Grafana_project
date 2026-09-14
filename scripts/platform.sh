#!/usr/bin/env bash
# Small, explicit operating commands for the manually prepared Azure TEST host.
# This script never creates Azure resources, formats disks, or deletes volumes.
set -euo pipefail

ROOT=/opt/observability
DIAGNOSTIC_IMAGE=python:3.14.7-slim-bookworm
if [[ ${EUID} -ne 0 ]]; then
  printf '%s\n' 'Run with sudo from /opt/observability.' >&2
  exit 1
fi
if [[ $(pwd -P) != "$ROOT" ]]; then
  printf '%s\n' 'First run: cd /opt/observability' >&2
  exit 1
fi
if [[ ! -f .env ]]; then
  printf '%s\n' 'Missing .env: follow docs/azure-and-linux.md first.' >&2
  exit 1
fi

compose=(docker compose --env-file "$ROOT/.env" -f "$ROOT/docker-compose.yml")
if [[ -f images.lock.yml ]]; then
  compose+=(-f "$ROOT/images.lock.yml")
fi

preflight() {
  echo 'Checking the host, mounted data disk, secret permissions, and Compose model.'
  [[ $(uname -s) == Linux && $(uname -m) == x86_64 ]] || {
    echo 'This deployment requires the planned Linux amd64 host.' >&2; return 1;
  }
  mountpoint --quiet /opt/observability-data || {
    echo 'Telemetry disk is not mounted. Do not start the containers.' >&2; return 1;
  }
  docker info >/dev/null
  "${compose[@]}" config --quiet
  "${compose[@]}" config --format json | python3 -c '
import ipaddress, json, os, pathlib, stat, subprocess, sys
model = json.load(sys.stdin)
services = model["services"]
expected = {"grafana": 472, "prometheus": 65534, "loki": 10001, "tempo": 10001, "alloy": 0}
data_root = pathlib.Path("/opt/observability-data")
for name, uid in expected.items():
    directory = data_root / name
    metadata = directory.stat()
    if not directory.is_dir() or directory.is_symlink() or metadata.st_uid != uid:
        sys.exit(f"Unexpected data directory ownership/type: {directory}; follow the host runbook.")
    state_mounts = [m for m in services[name]["volumes"] if m.get("source") == str(directory)]
    if len(state_mounts) != 1:
        sys.exit(f"{name}: data must use the planned mounted directory {directory}")
addresses = json.loads(subprocess.check_output(["ip", "-j", "-4", "address", "show"]))
host_ips = {a["local"] for item in addresses for a in item.get("addr_info", [])}
for port in services["alloy"]["ports"]:
    address = port.get("host_ip", "")
    parsed = ipaddress.ip_address(address)
    if address not in host_ips or not parsed.is_private or parsed.is_loopback or parsed.is_unspecified:
        sys.exit("Alloy must bind an assigned private VM IP, not a wildcard or loopback.")
secret = pathlib.Path(model["secrets"]["grafana_admin_password"]["file"])
metadata = secret.stat()
if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != 0 or metadata.st_gid != 472 or stat.S_IMODE(metadata.st_mode) != 0o640:
    sys.exit("Grafana secret must be a regular root:472 file with mode 0640.")
if metadata.st_size < 16:
    sys.exit("Grafana secret must contain at least 16 bytes.")
for path in ("/var/run/docker.sock", "/var/log/journal", "/etc/machine-id"):
    if not pathlib.Path(path).exists():
        sys.exit(f"Missing host mount source: {path}; follow the host runbook.")
print("PASS host and Compose preflight")
'
  systemctl is-active --quiet node-exporter.service || {
    echo 'Node Exporter is not active; inspect its systemd service.' >&2; return 1;
  }
}

validate_config() {
  echo 'Validating configuration with the pinned service binaries.'
  "${compose[@]}" config --quiet
  "${compose[@]}" run --rm --no-deps --entrypoint /bin/promtool prometheus check config /etc/prometheus/prometheus.yml
  "${compose[@]}" run --rm --no-deps loki -config.file=/etc/loki/loki-config.yaml -verify-config=true
  "${compose[@]}" run --rm --no-deps tempo -config.file=/etc/tempo/tempo.yaml -config.verify=true
  "${compose[@]}" run --rm --no-deps alloy validate /etc/alloy/config.alloy
}

probe() {
  docker run --rm --network observability-test \
    --mount "type=bind,source=$ROOT/scripts,target=/checks,readonly" \
    --mount "type=bind,source=$ROOT/validation-results,target=/results" \
    "$DIAGNOSTIC_IMAGE" python "/checks/$1" "${@:2}"
}

wait_ready() {
  local attempt
  for attempt in {1..18}; do
    if probe verify.py --services "$@"; then return 0; fi
    echo 'Waiting 5 seconds for this layer to become ready...'
    sleep 5
  done
  echo 'Layer did not become ready. Inspect Compose status and logs before continuing.' >&2
  return 1
}

case "${1:-help}" in
  preflight) preflight ;;
  validate-config) preflight; validate_config ;;
  start)
    case "${2:-}" in
      metrics) services=(prometheus cadvisor) ;;
      ui) services=(prometheus cadvisor grafana) ;;
      logs) services=(prometheus cadvisor grafana loki alloy) ;;
      traces|all) services=(prometheus cadvisor grafana loki tempo alloy) ;;
      *) echo 'Choose a layer: metrics, ui, logs, traces, or all.' >&2; exit 2 ;;
    esac
    preflight
    install -d -m 0750 validation-results
    "${compose[@]}" up -d "${services[@]}"
    wait_ready "${services[@]}"
    ;;
  stop) "${compose[@]}" stop ;;
  status) "${compose[@]}" ps ;;
  verify)
    install -d -m 0750 validation-results
    probe verify.py --targets
    ;;
  verify-datasources)
    secret_file=$("${compose[@]}" config --format json | python3 -c 'import json,sys; print(json.load(sys.stdin)["secrets"]["grafana_admin_password"]["file"])')
    docker run --rm --network observability-test \
      --mount "type=bind,source=$ROOT/scripts,target=/checks,readonly" \
      --mount "type=bind,source=$secret_file,target=/run/secrets/grafana_admin_password,readonly" \
      "$DIAGNOSTIC_IMAGE" python /checks/check_datasources.py
    ;;
  test-telemetry)
    install -d -m 0750 validation-results
    record="/results/probe-$(date -u +%Y%m%dT%H%M%S).json"
    probe telemetry_probe.py --record "$record"
    echo "Saved run evidence: validation-results/${record##*/}"
    ;;
  verify-record)
    [[ ${2:-} =~ ^probe-[0-9]{8}T[0-9]{6}\.json$ ]] || {
      echo 'Supply a probe-YYYYMMDDTHHMMSS.json filename from validation-results.' >&2; exit 2;
    }
    probe telemetry_probe.py --verify-only --record "/results/$2"
    ;;
  lock-images)
    if [[ -e images.lock.yml ]]; then
      "${compose[@]}" config --quiet
      echo 'Existing images.lock.yml preserved. Review it before a deliberate image update.'
      exit 0
    fi
    "${compose[@]}" config --lock-image-digests --output images.lock.yml
    echo 'Created images.lock.yml. Review and version-control this non-secret digest override.'
    ;;
  *)
    echo 'Usage: sudo bash scripts/platform.sh preflight|validate-config|start <layer>|stop|status|verify|verify-datasources|test-telemetry|verify-record <file>|lock-images'
    ;;
esac
