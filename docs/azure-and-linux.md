# Manual Azure and Linux foundation

This is an operator runbook, not evidence that Azure resources have been created. Run one section at a time on a **new** TEST VM. The old environment is not a cleanup target. All Linux commands below run as `grafana` on the new monitoring VM unless a block is explicitly marked **local PowerShell**.

The VM hosts the monitoring platform only. Application servers, dashboards, alert rules, and Ansible remain outside this phase. [Terraform](../terraform/README.md) now provides the Azure infrastructure: after a successful apply, skip section 1 and continue at section 2. Do not create the same resources through both methods.

## 1. Create the Azure resources manually

The capacity gives the telemetry backends space for a future microservices workload. Availability, quota, and price depend on the subscription: in the Azure portal, check UAE North availability for the exact SKU, the regional Dasv5 vCPU quota (at least 8 free vCPUs), and the cost estimate before creating resources. Include VM runtime, both disks, the public IP, snapshots, and outbound traffic. A stopped/deallocated VM still incurs charges for retained resources such as disks.

Use these portal settings:

| Resource | Setting |
| --- | --- |
| Subscription | Your intended TEST subscription; record its name privately |
| Region | UAE North; if the SKU or quota is unavailable, resolve that before creation |
| Resource group | `rg-observability-v2-test` |
| VNet | `vnet-observability-v2-test`, `10.20.0.0/16` |
| Subnet | `snet-monitoring-v2-test`, `10.20.0.0/24` |
| VM | `vm-monitoring-v2-test`, regular VM (not Spot) |
| Image | Ubuntu Server 24.04 LTS, x64, Generation 2 |
| Size | `Standard_D8as_v5`: 8 vCPUs, 32 GiB RAM |
| User and authentication | `grafana`, SSH public key; retain the private key outside the project |
| OS disk | 64 GiB Standard SSD, managed disk |
| Data disk | New empty 512 GiB Premium SSD P20, LUN 0, host caching `None` |
| Data disk name | `disk-observability-v2-test-data` |
| Private IP | Set the NIC IPv4 allocation to static, `10.20.0.4` |
| Public IP | Standard, static IPv4, `pip-observability-v2-test` |
| NSG | `nsg-observability-v2-test`, attached to the monitoring subnet |

Select **None** for the VM wizard's generic public inbound port option, and attach the prepared subnet/NSG. Use one NSG at the subnet rather than unintentionally attaching a second conflicting NSG to the NIC. Leave outbound connectivity available for package/image downloads and time synchronization.

Create these inbound NSG rules. Replace `ADMIN_PUBLIC_IP/32` with the administrator's real IPv4 address, not a literal placeholder. Destination port is shown; source port is `*` in every rule.

| Priority | Name | Source | Destination | Protocol/port | Action |
| --- | --- | --- | --- | --- | --- |
| 100 | `allow-admin-ssh` | `ADMIN_PUBLIC_IP/32` | `10.20.0.4` | TCP `22` | Allow |
| 110 | `allow-private-otlp` | `10.20.0.0/24` | `10.20.0.4` | TCP `4317,4318` | Allow |
| 4000 | `deny-other-inbound` | Any | Any | Any | Deny |

The priority-4000 denial overrides Azure's lower-priority default VNet allowance. Without it, neighboring VMs could reach unintended host ports. Existing allowed connections are stateful; use a new connection when testing a changed rule. See [Azure NSG behavior](https://learn.microsoft.com/en-us/azure/virtual-network/network-security-groups-overview).

The OTLP allowlist initially trusts this one subnet. Before adding a workload on another subnet, choose and document its explicit source CIDR. The current TEST receiver is private plaintext OTLP, without client authentication; do not expose it publicly or use it for untrusted clients.

Connect from the local machine using the private key stored outside the repository:

```powershell
ssh grafana@<MONITORING_PUBLIC_IP>
```

## 2. Verify the new host

These checks establish that you are preparing the intended empty host before installing anything:

```bash
hostnamectl
uname -m
free -h
ip -brief address
lsblk -o NAME,TYPE,SIZE,FSTYPE,MOUNTPOINTS,MODEL,SERIAL
timedatectl status
sudo systemctl status docker --no-pager
```

Expected: Ubuntu 24.04, `x86_64`, approximately 32 GiB RAM, private IP `10.20.0.4`, 64 GiB OS disk, and an empty 512 GiB data disk. Docker may not yet exist. If existing containers, mounted data, or unexpected disks are present, stop and inspect rather than applying fresh-install steps to them.

Install basic administration tools and enable time synchronization so log and trace timestamps can be compared:

```bash
sudo apt update
sudo apt install ca-certificates curl git jq iptables
sudo timedatectl set-ntp true
timedatectl status
```

## 3. Identify and mount only the new data disk

A persistent data disk keeps telemetry independent of the OS disk. Azure LUN 0 identifies the new disk; do not guess `/dev/sdb` or `/dev/sdc`, because device names can change. [Azure disk attachment guidance](https://learn.microsoft.com/en-us/azure/virtual-machines/linux/attach-disk-portal)

First run these read-only checks:

```bash
OBSERVABILITY_DISK=/dev/disk/azure/scsi1/lun0
ls -l /dev/disk/azure/scsi1/
readlink -f "$OBSERVABILITY_DISK"
lsblk -o NAME,TYPE,SIZE,FSTYPE,MOUNTPOINTS "$OBSERVABILITY_DISK"
sudo blockdev --getsize64 "$OBSERVABILITY_DISK"
sudo wipefs --no-act "$OBSERVABILITY_DISK"
findmnt /
```

Proceed only if this matches the newly attached LUN 0 disk, size `549755813888` bytes (512 GiB), with no filesystem signatures, partitions, mounted children, or other data. If the stable path is absent, inspect the Azure disk mapping; never substitute a guessed disk path.

**The next command formats that verified empty disk. Formatting erases existing data on the selected device and is only for the new disk's first setup. Never repeat it on a disk that contains platform data.** This layout uses an ext4 filesystem on the whole data disk, without a partition table:

```bash
sudo mkfs.ext4 -L observe-data "$OBSERVABILITY_DISK"
sudo install -d -m 0755 /opt/observability-data
sudo blkid "$OBSERVABILITY_DISK"
sudo cp --no-clobber --preserve /etc/fstab /etc/fstab.before-observability
sudoedit /etc/fstab
```

Add one line using the **actual UUID** reported by `blkid`; do not copy the placeholder literally:

```text
UUID=<ACTUAL_DATA_FILESYSTEM_UUID> /opt/observability-data ext4 defaults,noatime,nodev,nosuid,nofail,x-systemd.device-timeout=30s 0 2
```

`nofail` permits host recovery if the disk is unavailable. A separate Docker service requirement below prevents containers from starting against an unmounted directory on the OS disk.

Mount and confirm the source before creating any service data directories:

```bash
sudo systemctl daemon-reload
sudo mount /opt/observability-data
mountpoint /opt/observability-data
findmnt -o TARGET,SOURCE,FSTYPE,OPTIONS /opt/observability-data
df -h / /opt/observability-data
```

## 4. Transfer this project

The checkout holds configuration; generated telemetry stays on the mounted disk. Prepare its destination:

```bash
sudo install -d -o grafana -g grafana -m 0755 /opt/observability
```

From **local PowerShell** in the project folder, copy only the deployment files rather than uploading your private keys or local runtime files:

```powershell
scp -r .\docker-compose.yml .\images.lock.yml .\.env.example .\.gitignore .\.gitattributes .\README.md .\config .\host .\scripts .\docs grafana@<MONITORING_PUBLIC_IP>:/opt/observability/
```

Back on the VM:

```bash
cd /opt/observability
ls -la
```

Run all subsequent project Compose commands from `/opt/observability`, with `sudo`.

## 5. Install Docker and protect the data mount

Use Docker's official Ubuntu apt repository so Engine and the Compose plugin are installed together. These repository files target the selected Ubuntu 24.04 amd64 host. If `dpkg -l` shows an existing Docker installation, review it before continuing rather than removing packages blindly. [Docker Ubuntu installation](https://docs.docker.com/engine/install/ubuntu/)

```bash
dpkg -l docker.io docker-ce docker-compose-v2 containerd runc 2>/dev/null
sudo install -d -m 0755 /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod 0644 /etc/apt/keyrings/docker.asc
sudo install -m 0644 host/docker.sources /etc/apt/sources.list.d/docker.sources
sudo apt update
```

Install the mount guard **before** the Docker packages can start the daemon:

```bash
sudo install -d -m 0755 /etc/systemd/system/docker.service.d
sudo install -m 0644 host/docker.service.d/observability-data.conf /etc/systemd/system/docker.service.d/observability-data.conf
sudo systemctl daemon-reload
sudo apt install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo systemctl enable --now docker
sudo systemctl cat docker
sudo docker version
sudo docker compose version
dpkg-query -W docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
```

Record these installed package versions in the private deployment record. This manual first install uses the stable packages currently offered by the repository; container image versions are pinned separately in Compose. Schedule package upgrades deliberately and rerun validation after them.

Use `sudo docker`; do not add the login user to the Docker group for this build. Docker access grants effectively root-level control of the host.

## 6. Configure persistent Linux journals

Persistent journal files allow Alloy to collect host logs and resume after restarts. The supplied drop-in caps disk usage at 1 GiB:

```bash
sudo install -d -m 0755 /etc/systemd/journald.conf.d
sudo install -m 0644 host/journald.conf.d/observability.conf /etc/systemd/journald.conf.d/observability.conf
sudo systemd-tmpfiles --create --prefix /var/log/journal
sudo systemctl restart systemd-journald
sudo journalctl --flush
sudo journalctl --disk-usage
ls -ld /var/log/journal
```

Docker log rotation is configured per service in Compose. The OS disk also holds container images and Docker's bounded local logs, so inspect both OS and telemetry disk usage during operation.

## 7. Install Node Exporter and its network restriction

The exporter runs on the host to measure the actual Linux system. Download the pinned upstream binary and verify it against its release checksum manifest before installation. These commands are for amd64 only. [Node Exporter release](https://github.com/prometheus/node_exporter/releases/tag/v1.12.1)

```bash
OBSERVABILITY_NODE_WORK=$(mktemp -d)
cd "$OBSERVABILITY_NODE_WORK"
curl -fLO https://github.com/prometheus/node_exporter/releases/download/v1.12.1/node_exporter-1.12.1.linux-amd64.tar.gz
curl -fLO https://github.com/prometheus/node_exporter/releases/download/v1.12.1/sha256sums.txt
awk '$2 == "node_exporter-1.12.1.linux-amd64.tar.gz" { print }' sha256sums.txt | sha256sum --check --strict
```

Continue only after the checksum reports `OK`. An unavailable download or mismatch is a stop condition, not a reason to skip verification:

```bash
tar -xzf node_exporter-1.12.1.linux-amd64.tar.gz --no-same-owner
sudo install -m 0755 node_exporter-1.12.1.linux-amd64/node_exporter /usr/local/bin/node_exporter
/usr/local/bin/node_exporter --version
getent passwd node-exporter || sudo useradd --system --user-group --no-create-home --shell /usr/sbin/nologin node-exporter
cd /opt/observability
```

The exporter listens on IPv4 port 9100 before the Docker bridge exists. A dedicated host INPUT chain permits only loopback and traffic arriving from `br-observe` in `172.28.0.0/24`. Prometheus uses `172.28.0.1:9100`; the bridge name/subnet must match Compose. Existing NSG rules separately block external port 9100.

Inspect existing host firewall rules first. If another firewall manager is present, reconcile its reload behavior before installing these rules; a later ruleset replacement must not silently remove the exporter restriction. The supplied script adds only its own chain and one port-specific INPUT jump. It does not flush any rules or touch Docker's forwarding chains. Docker publishes ports through forwarding rules, so ordinary UFW rules alone are not sufficient to protect published container ports. [Docker firewall documentation](https://docs.docker.com/engine/network/packet-filtering-firewalls/)

```bash
sudo iptables -S INPUT
sudo systemctl is-active ufw firewalld nftables
sudo install -m 0755 host/node-exporter-firewall.sh /usr/local/sbin/observability-node-exporter-firewall
sudo install -m 0644 host/observability-node-exporter-firewall.service /etc/systemd/system/observability-node-exporter-firewall.service
sudo install -m 0644 host/node-exporter.service /etc/systemd/system/node-exporter.service
sudo systemctl daemon-reload
sudo systemctl enable --now observability-node-exporter-firewall.service
sudo systemctl enable --now node-exporter.service
sudo systemctl status node-exporter.service --no-pager
sudo iptables -S OBSERVABILITY-NODE
curl -fsS http://127.0.0.1:9100/metrics | head -n 10
```

The last command displays only the first metrics; `curl` can report a broken pipe when `head` exits after ten lines. Readiness is established by an HTTP success and by the Prometheus scrape during stack validation.

## 8. Prepare storage ownership and secrets

Each backend needs a writable directory owned by its container's numeric UID/GID. These values match the pinned Compose images; revisit them when changing images. `install -d` prepares only the named directories and does not recursively change existing files. Do not use `chmod 777` to work around a permissions error.

After confirming the data mount, create those directories:

```bash
cd /opt/observability
mountpoint /opt/observability-data && \
sudo install -d -o 472 -g 472 -m 0750 /opt/observability-data/grafana && \
sudo install -d -o 65534 -g 65534 -m 0750 /opt/observability-data/prometheus && \
sudo install -d -o 10001 -g 10001 -m 0750 /opt/observability-data/loki && \
sudo install -d -o 10001 -g 10001 -m 0750 /opt/observability-data/tempo && \
sudo install -d -o root -g root -m 0750 /opt/observability-data/alloy && \
sudo install -d -o 65534 -g 65534 -m 0750 /opt/observability-data/alertmanager
```

Check the selected mount and deployment parameters before the first start:

```bash
cd /opt/observability
mountpoint /opt/observability-data
sudo install -m 0600 -o grafana -g grafana .env.example .env
nano .env
```

Create `.env` from the example only on the first setup; do not overwrite a populated `.env` during updates. The private-IP setting must match `10.20.0.4` on this VM.

The Grafana password belongs in a separate ignored file. A Compose file-backed secret retains host file ownership, so the file is `root:472`, mode `0640`, inside a directory with mode `0750`. This lets Grafana's group read it while keeping it inaccessible to other host users. The following interactive command reads the password without echoing it or placing its value in shell history, and refuses to overwrite an existing password file:

```bash
sudo install -d -o root -g 472 -m 0750 /opt/observability/secrets
sudo bash -c '
set -euo pipefail
secret_file=/opt/observability/secrets/grafana-admin-password
if [[ -e "$secret_file" ]]; then
  printf "%s\n" "Password file already exists; preserve it during updates."
  exit 1
fi
read -r -s -p "New Grafana admin password (at least 16 characters): " observability_password
printf "\n"
if (( ${#observability_password} < 16 )); then
  printf "%s\n" "Password too short; no file created." >&2
  exit 1
fi
install -o root -g 472 -m 0640 /dev/null "$secret_file"
printf "%s" "$observability_password" > "$secret_file"
unset observability_password
'
sudo stat -c '%U:%G %a %n' secrets secrets/grafana-admin-password
sudo docker compose config --quiet
```

Save the password in team password storage. This initial password bootstraps a new Grafana database; editing the file later does not automatically rotate an existing user's password.

The layer commands run configuration checks and then start the selected components:

```bash
sudo bash scripts/platform.sh preflight
sudo bash scripts/platform.sh validate-config
sudo bash scripts/platform.sh start metrics
sudo bash scripts/platform.sh start ui
sudo bash scripts/platform.sh start logs
sudo bash scripts/platform.sh start traces
sudo bash scripts/platform.sh verify
sudo bash scripts/platform.sh verify-datasources
sudo bash scripts/platform.sh test-telemetry
```

Run and review one start command at a time. Prometheus knows about the eventual full stack: targets for services that have not been started yet are expected to be down during intermediate phases. Full-stack `verify` is meaningful after all phases have started. If a phase fails, inspect its state and logs before proceeding. Once validation passes, record image digests with `sudo bash scripts/platform.sh lock-images`.

## 9. Safe operation and backup

After each layer passes validation, start and stop the existing stack normally:

```bash
cd /opt/observability
sudo bash scripts/platform.sh status
sudo bash scripts/platform.sh stop
sudo bash scripts/platform.sh start all
```

Stopping services preserves their data. `docker compose down -v` is not a normal operation. Do not format or detach the live data disk, and do not test the missing-mount guard by unmounting a disk under running containers.

For a first manual backup, stop the stack to make a consistent cold data-disk snapshot, then use **Azure Portal → Disks → disk-observability-v2-test-data → Create snapshot**. Wait for success before starting the stack again. Snapshot the OS disk as well if you need to preserve the manual host installation. Store the Git revision, image digests, host package versions, and `.env`/secret files in appropriate secure storage; the telemetry disk alone does not contain every configuration or secret.

Test recovery on a **new** VM and a **new disk created from the snapshot**. Identify its existing filesystem and mount it without formatting, restore the matching configuration/secrets, then validate stored test telemetry. Do not claim backups are validated until this separate restore succeeds.

For overnight shutdown, stop Compose and then stop/deallocate the VM from the Azure portal; wait for `Stopped (deallocated)`. On startup, confirm the data mount, Docker, and Node Exporter, then run the stack's health validation. Maintain a record of the performed checks and measured capacity; creating the VM alone does not establish performance.
