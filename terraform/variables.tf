variable "subscription_id" {
  description = "Azure TEST subscription UUID. Authentication comes from Azure CLI, not this file."
  type        = string
  nullable    = false

  validation {
    condition     = can(regex("^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$", var.subscription_id))
    error_message = "subscription_id must be an Azure subscription UUID."
  }
}

variable "admin_source_cidr" {
  description = "Administrator's current internet-facing IPv4 address, restricted to /32."
  type        = string
  nullable    = false

  validation {
    condition = (
      can(cidrnetmask(var.admin_source_cidr)) &&
      can(regex("/32$", var.admin_source_cidr)) &&
      var.admin_source_cidr != "0.0.0.0/32"
    )
    error_message = "Use one valid administrator IPv4 address with /32; broad networks and 0.0.0.0 are forbidden."
  }
}

variable "admin_ssh_public_key" {
  description = "Complete single-line OpenSSH PUBLIC key. Never supply a private key."
  type        = string
  nullable    = false

  validation {
    condition     = can(regex("^(ssh-ed25519|ssh-rsa) [A-Za-z0-9+/]+={0,2}( [^\r\n]*)?$", trimspace(var.admin_ssh_public_key)))
    error_message = "Supply a single-line ssh-ed25519 or ssh-rsa public key, not a private key or file path."
  }
}

variable "ubuntu_image_version" {
  description = "Exact Canonical ubuntu-24_04-lts:server image version available in Central US; discover and pin before planning."
  type        = string
  nullable    = false

  validation {
    condition     = can(regex("^[0-9]+\\.[0-9]+\\.[0-9]+$", var.ubuntu_image_version))
    error_message = "Pin a numeric major.minor.patch image version; latest is not reproducible."
  }
}
