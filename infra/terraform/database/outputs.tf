output "endpoint" {
  description = "접속 주소 (호스트). 클러스터 안에서만 닿는다"
  value       = aws_db_instance.this.address
}

output "port" {
  value = aws_db_instance.this.port
}

output "master_username" {
  value = aws_db_instance.this.username
}

output "master_user_secret_arn" {
  description = "관리자 비밀번호가 든 Secrets Manager 시크릿 (RDS 관리, rds!db-...). DB·사용자 만들 때 한 번만 쓴다"
  value       = aws_db_instance.this.master_user_secret[0].secret_arn
}

output "security_group_id" {
  value = aws_security_group.rds.id
}

output "identifier" {
  description = "RDS 인스턴스 이름 (scripts/infra/dev-power.ps1 이 읽는다)"
  value       = aws_db_instance.this.identifier
}
