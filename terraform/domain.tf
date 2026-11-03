resource "aws_route53domains_domain" "site" {
  domain_name        = var.domain_name
  auto_renew         = true
  admin_privacy      = true
  registrant_privacy = true
  tech_privacy       = true
  transfer_lock      = true

  admin_contact {
    address_line_1 = var.registrant.address_line_1
    city           = var.registrant.city
    contact_type   = "PERSON"
    country_code   = var.registrant.country_code
    email          = var.registrant.email
    first_name     = var.registrant.first_name
    last_name      = var.registrant.last_name
    phone_number   = var.registrant.phone_number
    state          = var.registrant.state
    zip_code       = var.registrant.zip_code
  }

  registrant_contact {
    address_line_1 = var.registrant.address_line_1
    city           = var.registrant.city
    contact_type   = "PERSON"
    country_code   = var.registrant.country_code
    email          = var.registrant.email
    first_name     = var.registrant.first_name
    last_name      = var.registrant.last_name
    phone_number   = var.registrant.phone_number
    state          = var.registrant.state
    zip_code       = var.registrant.zip_code
  }

  tech_contact {
    address_line_1 = var.registrant.address_line_1
    city           = var.registrant.city
    contact_type   = "PERSON"
    country_code   = var.registrant.country_code
    email          = var.registrant.email
    first_name     = var.registrant.first_name
    last_name      = var.registrant.last_name
    phone_number   = var.registrant.phone_number
    state          = var.registrant.state
    zip_code       = var.registrant.zip_code
  }

  # duration_in_years renews the domain when it increases. Leave the registered
  # term alone after the first year is purchased.
  lifecycle {
    prevent_destroy = true
    ignore_changes  = [duration_in_years]
  }
}
