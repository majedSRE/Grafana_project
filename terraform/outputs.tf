output "resource_group_name" {
  description = "Dedicated TEST resource group."
  value       = azurerm_resource_group.monitoring.name
}

output "monitoring_public_ip" {
  description = "SSH endpoint; all other public inbound ports are blocked."
  value       = azurerm_public_ip.monitoring.ip_address
}

output "monitoring_private_ip" {
  description = "Matches MONITORING_PRIVATE_IP in the platform .env.example."
  value       = local.private_ip
}

output "ssh_command" {
  description = "Run locally after apply. Add -i with your private key path when necessary."
  value       = "ssh grafana@${azurerm_public_ip.monitoring.ip_address}"
}

output "grafana_tunnel_command" {
  description = "Use after the Linux runbook and platform installation, then browse http://localhost:3000."
  value       = "ssh -N -L 3000:127.0.0.1:3000 grafana@${azurerm_public_ip.monitoring.ip_address}"
}

output "telemetry_disk_id" {
  description = "Verify this disk and LUN 0 before the one-time manual formatting step."
  value       = azurerm_managed_disk.telemetry.id
}

output "ubuntu_image_version" {
  description = "Pinned base image for reproducibility."
  value       = var.ubuntu_image_version
}

output "next_step" {
  value = "Continue docs/azure-and-linux.md at section 2. Terraform does not format disks, install Docker, or start Grafana."
}
