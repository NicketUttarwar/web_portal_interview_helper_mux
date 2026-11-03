variable "aws_region" {
  type    = string
  default = "us-east-1"
}

variable "project_name" {
  type    = string
  default = "zero-shot-media-website"
}

variable "environment" {
  type    = string
  default = "prod"
}

variable "domain_name" {
  type = string
}

variable "s3_bucket_name" {
  type = string
}

variable "cloudfront_price_class" {
  type    = string
  default = "PriceClass_100"
}

variable "registrant" {
  description = "ICANN contact for the domain. The same contact is admin, registrant, and tech."
  type = object({
    first_name     = string
    last_name      = string
    email          = string
    phone_number   = string
    address_line_1 = string
    city           = string
    state          = string
    zip_code       = string
    country_code   = string
  })
  sensitive = true
}
