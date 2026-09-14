# Azure infrastructure with Terraform

Terraform creates the monitoring foundation only. It does not install Docker, format disks, deploy the external demo application, or configure dashboards and alerts. Keeping Linux setup in the existing runbook lets us validate one layer at a time.

## What it creates

| Resource | Fixed TEST configuration |
| --- | --- |
| Resource group | `rg-observability-v2-test`, UAE North (`uaenorth`) |
| VNet / subnet | `10.20.0.0/16` / `10.20.0.0/24` |
| Monitoring VM | `vm-monitoring-v2-test`, Ubuntu 24.04 Server, `Standard_D8as_v5` (8 vCPUs / 32 GiB) |
| OS / telemetry disks | 64 GiB Standard SSD / separate empty 512 GiB Premium SSD, LUN 0, caching None |
| Networking | Static private `10.20.0.4`, static Standard public IPv4, subnet NSG |
| Public inbound | SSH only from your supplied administrator IPv4 `/32` |
| Private inbound | OTLP TCP 4317/4318 from `10.20.0.0/24` only |
| Other inbound | Explicit denial, including the default VNet allowance |
| Administration | `grafana`, SSH public key only; Grafana later uses an SSH tunnel |

There are ten managed resources. The VM public IP provides explicit outbound connectivity; subnet default outbound access is disabled. No NAT gateway or public monitoring ports are added. See [Azure outbound access](https://learn.microsoft.com/en-us/azure/virtual-network/ip-services/default-outbound-access). The sizing is the project's initial target, not proof of future application capacity; see [capacity assumptions](../docs/capacity.md).

Files: `main.tf` defines resources, `variables.tf` defines required inputs, `outputs.tf` provides connection details, and `versions.tf` plus `.terraform.lock.hcl` pin the provider. Terraform CLI 1.15.6 is the validation baseline; the allowed range is declared in `versions.tf`. AzureRM is pinned to 5.5.0.

## 1. Prepare your local tools and Azure account

Run these steps on your local computer, not on the future VM. Install [Terraform](https://developer.hashicorp.com/terraform/install) and [Azure CLI](https://learn.microsoft.com/en-us/cli/azure/install-azure-cli-windows), then open a new PowerShell terminal.

```powershell
terraform version
az version
az login
az account list --output table
az account set --subscription '<YOUR_TEST_SUBSCRIPTION_UUID>'
az account show --query '{name:name,id:id,tenant:tenantId}' --output table
```

Check the selected subscription, UAE North SKU availability, at least 8 free regional Dasv5 vCPUs, and the Azure cost estimate before applying. This is a paid, single-VM TEST platform, not highly available production. Deallocation does not stop disk/public-IP charges.

Use read-only checks to examine quota and availability:

```powershell
az vm list-skus --location uaenorth --size Standard_D8as_v5 --all --output table
az vm list-usage --location uaenorth --output table
az group exists --name rg-observability-v2-test
```

If the group already exists, stop and inspect ownership. Do not apply against manually created resources blindly: controlled import and a reviewed no-destruction plan are required to adopt them. The old `rg-observability-test` deployment is outside this module.

Terraform does not automatically register subscription resource providers. Check them first:

```powershell
az provider show --namespace Microsoft.Compute --query registrationState --output tsv
az provider show --namespace Microsoft.Network --query registrationState --output tsv
```

Both must report `Registered`. If not, an authorized subscription administrator can register them. These commands change subscription registration, not VM resources:

```powershell
az provider register --namespace Microsoft.Compute --wait
az provider register --namespace Microsoft.Network --wait
```

Authentication uses your local Azure CLI session. No Azure credentials go in Terraform files. [AzureRM authentication documentation](https://registry.terraform.io/providers/hashicorp/azurerm/5.5.0/docs)

## 2. Set the four required inputs

From the repository root:

```powershell
Set-Location terraform
if (Test-Path -LiteralPath terraform.tfvars) { throw 'terraform.tfvars already exists; edit it instead of copying.' }
Copy-Item -LiteralPath terraform.tfvars.example -Destination terraform.tfvars
```

Copy only on first setup; do not overwrite an existing local `terraform.tfvars`. Edit that ignored file and replace every placeholder:

1. `subscription_id`: the TEST subscription UUID you selected.
2. `admin_source_cidr`: your current internet-facing IPv4 address followed by `/32`. Use your router/VPN's outbound public address, not your laptop's LAN address. Refresh it if your ISP/VPN address changes.
3. `admin_ssh_public_key`: the full single-line contents of your existing `.pub` file. Keep the corresponding private key outside this repository. No key pair is generated or stored by Terraform.
4. `ubuntu_image_version`: an exact available version from the command below. Do not use `latest`.

Read your existing public key using its actual path, for example:

```powershell
Get-Content -LiteralPath "$env:USERPROFILE\.ssh\id_ed25519.pub"
az vm image list --location uaenorth --publisher Canonical --offer ubuntu-24_04-lts --sku server --all --query '[].{version:version,urn:urn}' --output table
```

Choose an available current version and copy its numeric version into your local inputs. Verify the selected image is x64 / Generation 2:

```powershell
az vm image show --location uaenorth --urn 'Canonical:ubuntu-24_04-lts:server:<EXACT_VERSION>' --query '{architecture:architecture,generation:hyperVGeneration,version:name}' --output table
```

The version remains pinned across subsequent plans. Image changes can replace the VM, so review them separately. [Azure image discovery](https://learn.microsoft.com/en-us/azure/virtual-machines/linux/cli-ps-findimage)

## 3. Validate and review the plan

Initialization downloads the locked provider. Formatting, validation and mocked tests do not create Azure resources. Tests use synthetic inputs and a mocked provider, not your Azure account.

```powershell
terraform init
terraform fmt -check -recursive
terraform validate
terraform test
terraform plan -out=monitoring.tfplan
```

Review the complete plan: a fresh deployment should report **10 to add, 0 to change, 0 to destroy**. Check subscription, names, IP restrictions, image version, VM size and both disks. Do not continue if replacements or deletions appear unexpectedly. The real plan reads Azure and may fail on account permissions; mock tests cannot verify quota, region capacity, image availability or actual network behavior.

## 4. Create resources only after reviewing the plan

The following command creates billable Azure infrastructure. Applying a saved plan does not ask for a second interactive confirmation; run it only after approving the previous plan yourself.

```powershell
terraform apply monitoring.tfplan
terraform output
```

Do not reuse a saved plan after changing inputs. Generate and review a new one instead.

Connect using the `ssh_command` output, then continue [the Linux runbook at section 2](../docs/azure-and-linux.md#2-verify-the-new-host). Terraform attaches an **unformatted** telemetry disk; the runbook verifies its identity before first formatting. Never repeat formatting on a disk containing telemetry.

Only after Linux setup and platform startup will the `grafana_tunnel_command` output lead to a working Grafana instance.

## State, protection and recovery

- This initial single-operator TEST module uses local state. Keep `terraform.tfstate` and its backups secure and backed up outside Git. Losing state does not remove Azure resources; it removes Terraform's ownership record. Never start a second independent state against the same deployment.
- `.gitignore` excludes state, plans, local `.tfvars`, JSON variable files, and `.terraform/`. Keep `.terraform.lock.hcl` in version control: it contains provider checksums, not credentials. Treat plan/state contents as sensitive even though this module does not generate passwords or private keys.
- The telemetry disk has `prevent_destroy = true`. Terraform refuses plans that delete or replace it while that lifecycle rule remains in configuration. This is not an Azure resource lock, backup or protection against portal deletion or removal of its entire resource block.
- Normal shutdown is the platform's documented stop procedure, followed by Azure VM deallocation. Do not use `terraform destroy` for daily shutdown; it removes infrastructure and is intentionally blocked by disk protection.
- A deliberate teardown needs a separate reviewed backup/deletion procedure. Do not remove the protection just to make a plan pass.
- Keep a single operator for local state. Before team/CI use, migrate state to a separately bootstrapped, access-controlled Azure Blob backend with state locking; do not commit backend credentials.

## Verification status

Repository validation is recorded in [verification](../docs/verification.md). Azure plan/apply and host acceptance are still required. No resource creation is implied by the presence of these files or by passing mock tests.
