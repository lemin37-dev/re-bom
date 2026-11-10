output "web_url" {
  description = "밖에서 여는 주소"
  value       = "https://${aws_cloudfront_distribution.web.domain_name}"
}

output "distribution_id" {
  description = "캐시 무효화(create-invalidation) 때 쓰는 ID"
  value       = aws_cloudfront_distribution.web.id
}

output "distribution_arn" {
  description = "infra/terraform/storage 의 images_cdn_distribution_arn 에 넣는 값 (버킷 정책 OAC 허용)"
  value       = aws_cloudfront_distribution.web.arn
}
