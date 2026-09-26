# Getting Started with OctoSight

This is the authoritative first-time deployment journey for the reusable
OctoSight platform. It does not require an external application or workload.

## 1. Prerequisites

You provide:

- An Azure subscription and account with permission to create the resources.
- Azure CLI authentication, Terraform, Git, and an SSH client.
- An SSH key pair; the private key remains outside the repository.
- The deployment-specific Terraform values and an administrator IPv4 `/32`.
- A Grafana admin password of at least 16 characters.
- A Slack Incoming Webhook URL. The current checked-in Compose and
  Alertmanager configuration requires this secret file for the normal path.

The repository provides the Terraform definition, Linux procedures, Compose
stack, configuration, dashboards, rules, validation scripts, and CI checks.
GitHub Actions validates repository/configuration/tests; it does not deploy
Azure or run `terraform apply` automatically.

## 2. Clone the repository

Run locally:

```powershell
git clone <REPOSITORY_URL>
Set-Location Grafana_project
```

Expected result: the repository checkout contains `terraform/`,
`docker-compose.yml`, `config/`, `host/`, `scripts/`, `tests/`, and `docs/`.

## 3. Authenticate to Azure

```powershell
az login
az account list --output table
az account set --subscription '<AZURE_SUBSCRIPTION_ID>'
az account show --query '{name:name,id:id,tenant:tenantId}' --output table
```

USER INPUT REQUIRED: `<AZURE_SUBSCRIPTION_ID>`.

Expected result: `az account show` displays the subscription selected for this
deployment. Continue only when it is the intended account and subscription.

## 4. Prepare Terraform variables

```powershell
Set-Location terraform
Copy-Item -LiteralPath terraform.tfvars.example -Destination terraform.tfvars
```

Edit the ignored `terraform.tfvars` and replace every placeholder with:

- `subscription_id`: `<AZURE_SUBSCRIPTION_ID>`
- `admin_source_cidr`: `<ALLOWED_SSH_CIDR>` as one public IPv4 address with `/32`
- `admin_ssh_public_key`: `<SSH_PUBLIC_KEY>` as one OpenSSH public-key line
- `ubuntu_image_version`: an exact available numeric Ubuntu image version

Do not place passwords, private keys, state, or plan files in Git. The full
variable procedure and image discovery commands are in
[terraform/README.md](../terraform/README.md#2-set-the-four-required-terraform-inputs).

Expected result: `terraform.tfvars` exists only in the local ignored checkout
and contains no unreplaced placeholder.

## 5. Initialize and validate Terraform

From `terraform/`:

```powershell
terraform init
terraform fmt -check -recursive
terraform validate
terraform test
```

Expected result: initialization succeeds, formatting and validation pass, and
the mocked Terraform tests pass. These checks do not create Azure resources.

## 6. Plan the Azure infrastructure

```powershell
terraform plan -out=monitoring.tfplan
```

Review the plan before applying. The intended fresh deployment is ten
resources to add, with no changes or destroys. Confirm the subscription,
Central US location, restricted SSH source, private address, VM size, and
both disks. The plan file is local and sensitive; do not commit it.

Expected result: a reviewed plan for the OctoSight Azure foundation. Quota,
policy, image availability, and organization networking remain subscription-
specific checks.

## 7. Apply Terraform

Only after reviewing the plan:

```powershell
terraform apply monitoring.tfplan
terraform output
```

Expected result: Terraform creates the Azure foundation and prints the
resource group, public/private addresses, SSH command, tunnel command, disk
ID, and pinned image version. Capture the outputs privately.

## 8. Connect and prepare the Linux host

Use the `ssh_command` output, for example:

```powershell
ssh grafana@<MONITORING_PUBLIC_IP>
```

On the new Ubuntu host, follow [Azure and Linux](azure-and-linux.md) from
[section 2](azure-and-linux.md#2-verify-the-new-host) through section 8. That
runbook is the detailed procedure for checking the host, identifying the new
LUN 0 telemetry disk, mounting it safely, transferring the platform checkout,
installing Docker, configuring persistent journals, installing Node Exporter,
preparing service directories, and creating the Grafana secret.

USER INPUT REQUIRED during the runbook:

- `<MONITORING_PUBLIC_IP>` and the matching SSH private key.
- Confirmation that the attached disk is the new empty telemetry disk before
  the one-time format.
- Terraform fixes the monitoring VM private IP to `10.20.0.4` in
  `terraform/main.tf`; it is not a per-deployment variable in this module.
  Confirm the same value using the `monitoring_private_ip` Terraform output,
  then set `MONITORING_PRIVATE_IP` to that output in the platform `.env`.
  Also set `OBSERVABILITY_DATA_DIR=/opt/observability-data`.
- A Grafana password in the separate file referenced by
  `GRAFANA_ADMIN_PASSWORD_FILE`.
- Create your own Slack Incoming Webhook in Slack, keep its URL out of the
  repository, and store it using the existing `SLACK_WEBHOOK_FILE` mechanism.
  The provided `.env.example` sets `SLACK_WEBHOOK_FILE` to
  `/etc/octosight-slack-webhook`. On the VM, create the file and enter the
  value without displaying it in chat:

  ```bash
  sudo install -m 0600 /dev/null /etc/octosight-slack-webhook
  sudoedit /etc/octosight-slack-webhook
  ```

  Keep the path in `.env` consistent with that file. The current Compose
  configuration requires the file to exist before configuration validation.

Do not continue until the runbook's host, mount, permissions, Docker, and
Node Exporter checks pass.

## 9. Validate the platform configuration

Back on the VM, from `/opt/observability`:

```bash
cd /opt/observability
sudo bash scripts/platform.sh preflight
sudo bash scripts/platform.sh validate-config
```

Expected result: the host and Compose preflight pass, and the pinned
Prometheus, Loki, Tempo, and Alloy validators accept the configuration.

## 10. Start OctoSight

Start one layer at a time using the repository-supported procedure:

```bash
sudo bash scripts/platform.sh start metrics
sudo bash scripts/platform.sh start ui
sudo bash scripts/platform.sh start logs
sudo bash scripts/platform.sh start traces
```

Expected result: each selected layer starts and its readiness checks pass.
Targets for services not started yet may be down during intermediate layers.
After `traces`, the full platform is running.

## 11. Verify platform health and telemetry

```bash
sudo bash scripts/platform.sh status
sudo bash scripts/platform.sh verify
sudo bash scripts/platform.sh verify-datasources
sudo bash scripts/platform.sh test-telemetry
```

Expected result: all seven configured scrape jobs are present and up,
Grafana's Prometheus/Loki/Tempo datasource checks pass, and the synthetic
telemetry probe finds its records in the configured backends. The probe writes
its run record under the ignored `validation-results/` directory.

## 12. Access Grafana

From the local computer, use the Terraform tunnel output or:

```text
ssh -N -L 3000:127.0.0.1:3000 grafana@<MONITORING_PUBLIC_IP>
```

Open `http://localhost:3000` and sign in as `admin` with the operator-managed
Grafana password. Confirm the provisioned datasources and dashboards are
available.

## 13. Successful reproduction

OctoSight has been reproduced when Azure infrastructure exists, the Linux
host is prepared, the platform services are ready, the seven monitoring
targets are healthy, datasource validation passes, and the synthetic
telemetry validation passes. This is platform success; no external monitored
asset is required.

## 14. Optional next step: connect an asset

After successful platform validation, choose an integration pattern in
[Connect an Asset](connect-an-asset.md). That step is optional and depends on
the telemetry interfaces and network path provided by the asset.

For day-2 operation and troubleshooting, see [Operations](operations.md).
Evidence and known limitations are recorded in [Verification](verification.md).
