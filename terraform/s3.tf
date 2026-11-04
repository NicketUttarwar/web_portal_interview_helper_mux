resource "aws_s3_bucket" "zero_shot_media_website" {
  bucket = var.s3_bucket_name

  tags = local.tags
}

resource "aws_s3_bucket_public_access_block" "zero_shot_media_website" {
  bucket = aws_s3_bucket.zero_shot_media_website.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "zero_shot_media_website" {
  bucket = aws_s3_bucket.zero_shot_media_website.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_cors_configuration" "zero_shot_media_website" {
  bucket = aws_s3_bucket.zero_shot_media_website.id

  cors_rule {
    allowed_headers = ["*"]
    allowed_methods = ["GET", "HEAD", "POST", "PUT"]
    allowed_origins = ["https://zeroshot.media"]
    expose_headers  = ["ETag"]
    max_age_seconds = 300
  }
}

resource "aws_s3_object" "index" {
  bucket       = aws_s3_bucket.zero_shot_media_website.id
  key          = "index.html"
  source       = "${path.module}/../site/index.html"
  etag         = filemd5("${path.module}/../site/index.html")
  content_type = "text/html; charset=utf-8"
}

resource "aws_s3_object" "hero" {
  bucket       = aws_s3_bucket.zero_shot_media_website.id
  key          = "hero.jpg"
  source       = "${path.module}/../site/hero.jpg"
  etag         = filemd5("${path.module}/../site/hero.jpg")
  content_type = "image/jpeg"
}
