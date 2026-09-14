terraform {
  required_version = ">= 1.15.6, < 2.0.0"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "5.5.0"
    }
  }
}
