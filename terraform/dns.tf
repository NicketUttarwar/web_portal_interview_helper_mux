resource "aws_route53_record" "site_a" {
  zone_id = aws_route53domains_domain.site.hosted_zone_id
  name    = var.domain_name
  type    = "A"

  alias {
    name                   = aws_cloudfront_distribution.zero_shot_media_website.domain_name
    zone_id                = aws_cloudfront_distribution.zero_shot_media_website.hosted_zone_id
    evaluate_target_health = false
  }
}

resource "aws_route53_record" "site_aaaa" {
  zone_id = aws_route53domains_domain.site.hosted_zone_id
  name    = var.domain_name
  type    = "AAAA"

  alias {
    name                   = aws_cloudfront_distribution.zero_shot_media_website.domain_name
    zone_id                = aws_cloudfront_distribution.zero_shot_media_website.hosted_zone_id
    evaluate_target_health = false
  }
}
