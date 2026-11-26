output "data_bucket" {
  description = ".env 의 REBOM_S3_BUCKET 에 넣는 값"
  value       = aws_s3_bucket.this["data"].id
}

output "data_bucket_regional_domain_name" {
  description = "CloudFront /images/* origin 에 넣는 주소"
  value       = aws_s3_bucket.this["data"].bucket_regional_domain_name
}
