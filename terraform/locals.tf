locals {
  name = "zero-shot-media-website"
  tags = {
    Name        = local.name
    Project     = var.project_name
    Environment = var.environment
  }
}
