data "aws_cloudfront_cache_policy" "caching_disabled" {
  name = "Managed-CachingDisabled"
}

resource "aws_cloudfront_origin_access_control" "zero_shot_media_website" {
  name                              = local.name
  description                       = "Origin access control for the zero shot media website"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

resource "aws_cloudfront_distribution" "zero_shot_media_website" {
  enabled             = true
  is_ipv6_enabled     = true
  comment             = local.name
  default_root_object = "index.html"
  price_class         = var.cloudfront_price_class
  aliases             = [var.domain_name]

  origin {
    domain_name              = aws_s3_bucket.zero_shot_media_website.bucket_regional_domain_name
    origin_id                = local.name
    origin_access_control_id = aws_cloudfront_origin_access_control.zero_shot_media_website.id
  }

  default_cache_behavior {
    allowed_methods        = ["GET", "HEAD", "OPTIONS"]
    cached_methods         = ["GET", "HEAD"]
    target_origin_id       = local.name
    cache_policy_id        = data.aws_cloudfront_cache_policy.caching_disabled.id
    viewer_protocol_policy = "redirect-to-https"
    compress               = true
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    acm_certificate_arn      = aws_acm_certificate_validation.zero_shot_media_website.certificate_arn
    ssl_support_method       = "sni-only"
    minimum_protocol_version = "TLSv1.2_2021"
  }

  tags = local.tags

  depends_on = [aws_s3_bucket_public_access_block.zero_shot_media_website]
}

data "aws_iam_policy_document" "zero_shot_media_website" {
  statement {
    sid    = "AllowCloudFrontRead"
    effect = "Allow"

    principals {
      type        = "Service"
      identifiers = ["cloudfront.amazonaws.com"]
    }

    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.zero_shot_media_website.arn}/*"]

    condition {
      test     = "StringEquals"
      variable = "AWS:SourceArn"
      values   = [aws_cloudfront_distribution.zero_shot_media_website.arn]
    }
  }
}

resource "aws_s3_bucket_policy" "zero_shot_media_website" {
  bucket = aws_s3_bucket.zero_shot_media_website.id
  policy = data.aws_iam_policy_document.zero_shot_media_website.json

  depends_on = [aws_s3_bucket_public_access_block.zero_shot_media_website]
}
