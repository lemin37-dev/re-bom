output "repository_urls" {
  description = "이미지 주소. DAG 와 빌드 워크플로에 쓴다"
  value       = { for k, r in aws_ecr_repository.this : k => r.repository_url }
}

output "github_push_role_arn" {
  description = "빌드 워크플로의 aws-actions/configure-aws-credentials role-to-assume 에 넣는 값"
  value       = aws_iam_role.github_push.arn
}
