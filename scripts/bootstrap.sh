#!/usr/bin/env bash
# Prepare a new Ubuntu Azure VM and start the existing observability stack.
#
# This script is intentionally conservative. It never selects or formats a disk
# implicitly, never overwrites .env or an existing Grafana secret, and never
# creates Azure resources. Run it from /opt/observability as root after copying
# this repository to that directory.
set -Eeuo pipefail

ROOT=/opt/observability
DATA_ROOT=/opt/observability-data
NODE_EXPORTER_VERSION=1.12.1
NODE_EXPORTER_ARCHIVE="node_exporter-${NODE_EXPORTER_VERSION}.linux-amd64.tar.gz"
NODE_EXPORTER_URL="https://github.com/prometheus/node_exporter/releases/download/v${NODE_EXPORTER_VERSION}"

fail() {
  printf 'FAIL: %s\n' "$*" >&2
  exit 1
}

on_error() {
  printf 'FAIL: bootstrap stopped at line %s. Review the command above and the host state before retrying.\n' "$1" >&2
}
trap 'on_error "$LINENO"' ERR

[[ ${EUID} -eq 0 ]] || fail 'Run with sudo or as root.'
[[ $(pwd -P) == "$ROOT" ]] || fail "Run from $ROOT."
[[ -f "$ROOT/docker-compose.yml" ]] || fail "Missing $ROOT/docker-compose.yml; copy the project before bootstrapping."
[[ -f "$ROOT/images.lock.yml" ]] || fail "Missing $ROOT/images.lock.yml; the pinned image lock file is required."

source /etc/os-release
[[ ${ID:-} == ubuntu && ${VERSION_ID:-} == 24.04 ]] || \
  fail "Supported host is Ubuntu 24.04; found ${PRETTY_NAME:-unknown}."
[[ $(uname -m) == x86_64 ]] || fail "Supported host architecture is x86_64; found $(uname -m)."

printf '%s\n' 'PASS: supported Ubuntu 24.04 x86_64 host'

install_packages() {
  export DEBIAN_FRONTEND=noninteractive
  apt-get update
  apt-get install -y ca-certificates curl git jq iptables gnupg python3
}

install_docker() {
  if ! command -v docker >/dev/null 2>&1 || ! docker compose version >/dev/null 2>&1; then
    install -d -m 0755 /etc/apt/keyrings
    if [[ ! -s /etc/apt/keyrings/docker.asc ]]; then
      curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
      chmod 0644 /etc/apt/keyrings/docker.asc
    fi
    install -m 0644 "$ROOT/host/docker.sources" /etc/apt/sources.list.d/docker.sources
    apt-get update
    apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  fi

  install -d -m 0755 /etc/systemd/system/docker.service.d
  install -m 0644 "$ROOT/host/docker.service.d/observability-data.conf" \
    /etc/systemd/system/docker.service.d/observability-data.conf
  systemctl daemon-reload
  systemctl enable --now docker
  docker info >/dev/null
  docker compose version >/dev/null
}

prepare_data_disk() {
  install -d -m 0755 "$DATA_ROOT"

  if mountpoint --quiet "$DATA_ROOT"; then
    local mounted_source
    mounted_source=$(findmnt -no SOURCE --target "$DATA_ROOT")
    [[ -n "$mounted_source" ]] || fail "Data path is mounted but its source could not be identified."
    printf 'PASS: telemetry disk already mounted at %s from %s; no formatting performed\n' "$DATA_ROOT" "$mounted_source"
    return
  fi

  : "${TELEMETRY_DISK_DEVICE:?Set TELEMETRY_DISK_DEVICE to the reviewed Azure data disk, for example /dev/sdc.}"
  local device root_source existing_mount filesystem uuid fstab_line
  device=$(readlink -f "$TELEMETRY_DISK_DEVICE")
  [[ -b "$device" ]] || fail "TELEMETRY_DISK_DEVICE is not a block device: $device"

  root_source=$(readlink -f "$(findmnt -no SOURCE /)")
  [[ "$device" != "$root_source" ]] || fail "Refusing to use the root disk as telemetry storage: $device"
  existing_mount=$(lsblk -nrpo MOUNTPOINT "$device" | sed '/^$/d' | head -n 1 || true)
  [[ -z "$existing_mount" ]] || fail "Telemetry disk is already mounted at $existing_mount; inspect it before retrying."

  filesystem=$(blkid -o value -s TYPE "$device" || true)
  if [[ -z "$filesystem" ]]; then
    [[ ${ALLOW_EMPTY_DISK_FORMAT:-0} == 1 ]] || \
      fail "Disk has no filesystem. Set ALLOW_EMPTY_DISK_FORMAT=1 only after confirming it is the new empty Azure disk."
    [[ -z "$(wipefs -n "$device" 2>/dev/null || true)" ]] || \
      fail "Disk contains filesystem signatures; refusing to format it."
    mkfs.ext4 -L observe-data "$device"
    filesystem=ext4
  fi
  [[ "$filesystem" == ext4 ]] || fail "Telemetry disk must use ext4; found $filesystem on $device."

  uuid=$(blkid -o value -s UUID "$device")
  fstab_line="UUID=$uuid $DATA_ROOT ext4 defaults,noatime,nodev,nosuid,nofail,x-systemd.device-timeout=30s 0 2"
  if grep -Eq "[[:space:]]$DATA_ROOT[[:space:]]" /etc/fstab; then
    grep -Fqx "$fstab_line" /etc/fstab || fail "Existing /etc/fstab entry for $DATA_ROOT does not match the reviewed disk UUID."
  else
    cp --preserve=mode,ownership,timestamps /etc/fstab "/etc/fstab.before-observability.$(date -u +%Y%m%dT%H%M%SZ)"
    printf '%s\n' "$fstab_line" >> /etc/fstab
  fi
  mount "$DATA_ROOT"
  mountpoint --quiet "$DATA_ROOT" || fail "Telemetry disk did not mount at $DATA_ROOT."
  printf 'PASS: telemetry disk mounted at %s from %s\n' "$DATA_ROOT" "$device"
}

configure_journal() {
  install -d -m 0755 /etc/systemd/journald.conf.d
  install -m 0644 "$ROOT/host/journald.conf.d/observability.conf" \
    /etc/systemd/journald.conf.d/observability.conf
  systemctl restart systemd-journald
  journalctl --flush
}

install_node_exporter() {
  if [[ ! -x /usr/local/bin/node_exporter ]]; then
    local work checksum_line
    work=$(mktemp -d)
    trap 'rm -rf "$work"' RETURN
    curl -fL "$NODE_EXPORTER_URL/$NODE_EXPORTER_ARCHIVE" -o "$work/$NODE_EXPORTER_ARCHIVE"
    curl -fL "$NODE_EXPORTER_URL/sha256sums.txt" -o "$work/sha256sums.txt"
    checksum_line=$(awk -v archive="$NODE_EXPORTER_ARCHIVE" '$2 == archive { print }' "$work/sha256sums.txt")
    [[ -n "$checksum_line" ]] || fail "Checksum entry for $NODE_EXPORTER_ARCHIVE was not found."
    printf '%s\n' "$checksum_line" | (cd "$work" && sha256sum --check --strict)
    tar -xzf "$work/$NODE_EXPORTER_ARCHIVE" -C "$work" --no-same-owner
    install -m 0755 "$work/node_exporter-${NODE_EXPORTER_VERSION}.linux-amd64/node_exporter" \
      /usr/local/bin/node_exporter
    rm -rf "$work"
    trap - RETURN
  fi

  getent passwd node-exporter >/dev/null || \
    useradd --system --user-group --no-create-home --shell /usr/sbin/nologin node-exporter
  install -m 0755 "$ROOT/host/node-exporter-firewall.sh" \
    /usr/local/sbin/observability-node-exporter-firewall
  install -m 0644 "$ROOT/host/observability-node-exporter-firewall.service" \
    /etc/systemd/system/observability-node-exporter-firewall.service
  install -m 0644 "$ROOT/host/node-exporter.service" \
    /etc/systemd/system/node-exporter.service
  systemctl daemon-reload
  systemctl enable --now observability-node-exporter-firewall.service
  systemctl enable --now node-exporter.service
  systemctl is-active --quiet node-exporter.service
}

prepare_compose_environment() {
  local secret_file secret_path
  if [[ ! -f "$ROOT/.env" ]]; then
    install -m 0600 "$ROOT/.env.example" "$ROOT/.env"
  fi
  grep -Fqx 'MONITORING_PRIVATE_IP=10.20.0.4' "$ROOT/.env" || \
    fail '.env must set MONITORING_PRIVATE_IP=10.20.0.4.'
  grep -Fqx 'OBSERVABILITY_DATA_DIR=/opt/observability-data' "$ROOT/.env" || \
    fail '.env must set OBSERVABILITY_DATA_DIR=/opt/observability-data.'
  grep -Eq '^GRAFANA_ADMIN_PASSWORD_FILE=.+$' "$ROOT/.env" || \
    fail '.env must define GRAFANA_ADMIN_PASSWORD_FILE.'

  secret_path=$(awk -F= '$1 == "GRAFANA_ADMIN_PASSWORD_FILE" {print substr($0, index($0, "=") + 1)}' "$ROOT/.env")
  if [[ "$secret_path" = /* ]]; then secret_file="$secret_path"; else secret_file="$ROOT/$secret_path"; fi
  [[ -f "$secret_file" ]] || \
    fail "Grafana secret is missing: $secret_file. Create a strong secret of at least 16 characters; this script will not generate a default password."
  [[ -f "$secret_file" && ! -L "$secret_file" ]] || fail "Grafana secret must be a regular file: $secret_file"
  [[ $(stat -c '%u:%g:%a' "$secret_file") == 0:472:640 ]] || \
    fail "Grafana secret must be owned root:472 with mode 0640: $secret_file"
  docker compose --env-file "$ROOT/.env" -f "$ROOT/docker-compose.yml" -f "$ROOT/images.lock.yml" config --quiet
}

prepare_directories() {
  install -d -o 472 -g 472 -m 0750 "$DATA_ROOT/grafana"
  install -d -o 65534 -g 65534 -m 0750 "$DATA_ROOT/prometheus"
  install -d -o 10001 -g 10001 -m 0750 "$DATA_ROOT/loki"
  install -d -o 10001 -g 10001 -m 0750 "$DATA_ROOT/tempo"
  install -d -o root -g root -m 0750 "$DATA_ROOT/alloy"
  install -d -o 65534 -g 65534 -m 0750 "$DATA_ROOT/alertmanager"
  install -d -o root -g 472 -m 0750 "$ROOT/secrets"
}

install_packages
prepare_data_disk
install_docker
configure_journal
prepare_directories
install_node_exporter
prepare_compose_environment

printf '%s\n' 'Starting the pinned observability stack and running existing validation...'
bash "$ROOT/scripts/platform.sh" validate-config
bash "$ROOT/scripts/platform.sh" start all
bash "$ROOT/scripts/platform.sh" verify
bash "$ROOT/scripts/platform.sh" verify-datasources

printf '%s\n' 'PASS: Ubuntu host, telemetry disk, Docker, journal, Node Exporter, Compose, services, and validation are complete.'
