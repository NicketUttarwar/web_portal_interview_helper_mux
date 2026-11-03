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

output "site_url" {
  value = "https://${var.domain_name}"
}

output "s3_bucket_name" {
  value = aws_s3_bucket.zero_shot_media_website.bucket
}

output "cloudfront_distribution_id" {
  value = aws_cloudfront_distribution.zero_shot_media_website.id
}

output "cloudfront_domain_name" {
  value = aws_cloudfront_distribution.zero_shot_media_website.domain_name
}

output "certificate_arn" {
  value = aws_acm_certificate.zero_shot_media_website.arn
}
