# Fixed TEST architecture: keep host instructions and .env.example in sync if changed.
locals {
  location   = "centralus"
  private_ip = "10.20.0.4"
  tags = {
    project     = "grafana-observability"
    environment = "test"
    managed_by  = "terraform"
  }
}

resource "azurerm_resource_group" "monitoring" {
  name     = "rg-observability-v2-test"
  location = local.location
  tags     = local.tags
}

resource "azurerm_virtual_network" "monitoring" {
  name                = "vnet-observability-v2-test"
  location            = azurerm_resource_group.monitoring.location
  resource_group_name = azurerm_resource_group.monitoring.name
  address_space       = ["10.20.0.0/16"]
  tags                = local.tags
}

resource "azurerm_subnet" "monitoring" {
  name                            = "snet-monitoring-v2-test"
  resource_group_name             = azurerm_resource_group.monitoring.name
  virtual_network_name            = azurerm_virtual_network.monitoring.name
  address_prefixes                = ["10.20.0.0/24"]
  default_outbound_access_enabled = false
}

resource "azurerm_network_security_group" "monitoring" {
  name                = "nsg-observability-v2-test"
  location            = azurerm_resource_group.monitoring.location
  resource_group_name = azurerm_resource_group.monitoring.name
  tags                = local.tags

  security_rule {
    name                       = "allow-admin-ssh"
    priority                   = 100
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_port_range          = "*"
    destination_port_range     = "22"
    source_address_prefix      = var.admin_source_cidr
    destination_address_prefix = local.private_ip
  }

  security_rule {
    name                       = "allow-private-otlp"
    priority                   = 110
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_port_range          = "*"
    destination_port_ranges    = ["4317", "4318"]
    source_address_prefix      = "10.20.0.0/24"
    destination_address_prefix = local.private_ip
  }

  # Overrides Azure's default AllowVNetInBound as well as public inbound access.
  security_rule {
    name                       = "deny-other-inbound"
    priority                   = 4000
    direction                  = "Inbound"
    access                     = "Deny"
    protocol                   = "*"
    source_port_range          = "*"
    destination_port_range     = "*"
    source_address_prefix      = "*"
    destination_address_prefix = "*"
  }
}

resource "azurerm_subnet_network_security_group_association" "monitoring" {
  subnet_id                 = azurerm_subnet.monitoring.id
  network_security_group_id = azurerm_network_security_group.monitoring.id
}

resource "azurerm_public_ip" "monitoring" {
  name                = "pip-observability-v2-test"
  location            = azurerm_resource_group.monitoring.location
  resource_group_name = azurerm_resource_group.monitoring.name
  allocation_method   = "Static"
  sku                 = "Standard"
  ip_version          = "IPv4"
  tags                = local.tags
}

resource "azurerm_network_interface" "monitoring" {
  name                = "nic-observability-v2-test"
  location            = azurerm_resource_group.monitoring.location
  resource_group_name = azurerm_resource_group.monitoring.name
  tags                = local.tags

  ip_configuration {
    name                          = "primary"
    primary                       = true
    subnet_id                     = azurerm_subnet.monitoring.id
    private_ip_address_allocation = "Static"
    private_ip_address            = local.private_ip
    public_ip_address_id          = azurerm_public_ip.monitoring.id
  }

  # No VM can be attached before the subnet's restrictive NSG is in place.
  depends_on = [azurerm_subnet_network_security_group_association.monitoring]
}

resource "azurerm_linux_virtual_machine" "monitoring" {
  name                            = "vm-monitoring-v2-test"
  computer_name                   = "vm-monitoring-v2-test"
  location                        = azurerm_resource_group.monitoring.location
  resource_group_name             = azurerm_resource_group.monitoring.name
  size                            = "Standard_E4as_v7"
  admin_username                  = "grafana"
  disable_password_authentication = true
  network_interface_ids           = [azurerm_network_interface.monitoring.id]
  provision_vm_agent              = true
  priority                        = "Regular"
  tags                            = local.tags

  admin_ssh_key {
    username   = "grafana"
    public_key = trimspace(var.admin_ssh_public_key)
  }

  os_disk {
    name                 = "disk-observability-v2-test-os"
    caching              = "ReadWrite"
    storage_account_type = "StandardSSD_LRS"
    disk_size_gb         = 64
  }

  source_image_reference {
    publisher = "Canonical"
    offer     = "ubuntu-24_04-lts"
    sku       = "server"
    version   = var.ubuntu_image_version
  }

  boot_diagnostics {}
}

resource "azurerm_managed_disk" "telemetry" {
  name                 = "disk-observability-v2-test-data"
  location             = azurerm_resource_group.monitoring.location
  resource_group_name  = azurerm_resource_group.monitoring.name
  storage_account_type = "Premium_LRS"
  create_option        = "Empty"
  disk_size_gb         = 256
  tags                 = local.tags

  lifecycle {
    prevent_destroy = true
  }
}

resource "azurerm_virtual_machine_data_disk_attachment" "telemetry" {
  managed_disk_id    = azurerm_managed_disk.telemetry.id
  virtual_machine_id = azurerm_linux_virtual_machine.monitoring.id
  lun                = 0
  caching            = "None"
}
