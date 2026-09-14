# Mock plans exercise the actual provider schema without credentials or Azure writes.
mock_provider "azurerm" {}

variables {
  subscription_id   = "11111111-1111-1111-1111-111111111111"
  admin_source_cidr = "203.0.113.10/32"
  # Public-only test fixture, not an operator login key.
  admin_ssh_public_key = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAABAgMEBQYHCAkKCwwNDg8QERITFBUWFxgZGhscHR4f test-only"
  ubuntu_image_version = "24.04.202601010"
}

run "capacity_and_storage" {
  command = plan

  assert {
    condition     = azurerm_linux_virtual_machine.monitoring.size == "Standard_D8as_v5"
    error_message = "The baseline must retain the agreed 8-vCPU / 32-GiB VM size."
  }
  assert {
    condition     = azurerm_linux_virtual_machine.monitoring.os_disk[0].disk_size_gb == 64 && azurerm_linux_virtual_machine.monitoring.os_disk[0].storage_account_type == "StandardSSD_LRS"
    error_message = "The OS disk must be a separate 64-GiB Standard SSD."
  }
  assert {
    condition     = azurerm_managed_disk.telemetry.disk_size_gb == 512 && azurerm_managed_disk.telemetry.storage_account_type == "Premium_LRS" && azurerm_managed_disk.telemetry.create_option == "Empty"
    error_message = "The telemetry disk must be a new 512-GiB Premium SSD."
  }
  assert {
    condition     = azurerm_virtual_machine_data_disk_attachment.telemetry.lun == 0 && azurerm_virtual_machine_data_disk_attachment.telemetry.caching == "None"
    error_message = "The Linux runbook requires data LUN 0 with caching None."
  }
  assert {
    condition     = azurerm_linux_virtual_machine.monitoring.source_image_reference[0].version == var.ubuntu_image_version && azurerm_linux_virtual_machine.monitoring.source_image_reference[0].sku == "server"
    error_message = "Use the pinned Ubuntu Server image."
  }
}

run "network_and_access" {
  command = plan

  assert {
    condition     = azurerm_network_interface.monitoring.ip_configuration[0].private_ip_address == "10.20.0.4" && azurerm_network_interface.monitoring.ip_configuration[0].private_ip_address_allocation == "Static"
    error_message = "The static private IP must match the Compose configuration."
  }
  assert {
    condition     = azurerm_subnet.monitoring.address_prefixes == tolist(["10.20.0.0/24"]) && !azurerm_subnet.monitoring.default_outbound_access_enabled
    error_message = "Use the intended subnet and explicit public-IP outbound connectivity."
  }
  assert {
    condition     = azurerm_public_ip.monitoring.sku == "Standard" && azurerm_public_ip.monitoring.allocation_method == "Static"
    error_message = "Use a static Standard public IP."
  }
  assert {
    condition     = azurerm_linux_virtual_machine.monitoring.disable_password_authentication && azurerm_linux_virtual_machine.monitoring.admin_username == "grafana"
    error_message = "Only SSH-key authentication is permitted for the grafana administrator."
  }
  assert {
    condition     = length(azurerm_network_security_group.monitoring.security_rule) == 3
    error_message = "The foundation has exactly the three reviewed inbound rules."
  }
  assert {
    condition = alltrue([for rule in azurerm_network_security_group.monitoring.security_rule :
      rule.direction == "Inbound" && (
        (rule.name == "allow-admin-ssh" && rule.priority == 100 && rule.access == "Allow" && rule.protocol == "Tcp" && rule.source_address_prefix == var.admin_source_cidr && rule.destination_address_prefix == "10.20.0.4" && rule.destination_port_range == "22") ||
        (rule.name == "allow-private-otlp" && rule.priority == 110 && rule.access == "Allow" && rule.protocol == "Tcp" && rule.source_address_prefix == "10.20.0.0/24" && rule.destination_address_prefix == "10.20.0.4" && toset(rule.destination_port_ranges) == toset(["4317", "4318"])) ||
        (rule.name == "deny-other-inbound" && rule.priority == 4000 && rule.access == "Deny" && rule.protocol == "*" && rule.source_address_prefix == "*" && rule.destination_address_prefix == "*" && rule.destination_port_range == "*")
      )
    ])
    error_message = "Allow only administrator SSH and subnet OTLP, then deny everything else inbound."
  }
}

run "reject_open_ssh" {
  command = plan
  variables {
    admin_source_cidr = "0.0.0.0/0"
  }
  expect_failures = [var.admin_source_cidr]
}

run "reject_invalid_ipv4" {
  command = plan
  variables {
    admin_source_cidr = "999.1.1.1/32"
  }
  expect_failures = [var.admin_source_cidr]
}

run "reject_private_key" {
  command = plan
  variables {
    admin_ssh_public_key = "-----BEGIN OPENSSH PRIVATE KEY-----"
  }
  expect_failures = [var.admin_ssh_public_key]
}

run "reject_unpinned_image" {
  command = plan
  variables {
    ubuntu_image_version = "latest"
  }
  expect_failures = [var.ubuntu_image_version]
}

run "reject_invalid_subscription" {
  command = plan
  variables {
    subscription_id = "replace-me"
  }
  expect_failures = [var.subscription_id]
}
