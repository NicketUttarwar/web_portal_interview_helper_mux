output "domain_name" {
  value = aws_route53domains_domain.site.domain_name
}

output "auto_renew" {
  value = aws_route53domains_domain.site.auto_renew
}

output "expiration_date" {
  value = aws_route53domains_domain.site.expiration_date
}

output "hosted_zone_id" {
  value = aws_route53domains_domain.site.hosted_zone_id
}

output "creation_date" {
  value = aws_route53domains_domain.site.creation_date
}
