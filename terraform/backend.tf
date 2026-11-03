# Live state path is relative to this directory.
# config/terraform.tfvars and config/secrets/secrets.env stay uncommitted.
terraform {
  backend "local" {
    path = "state/terraform.tfstate"
  }
}
