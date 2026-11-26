variable "account_id" {
  description = "re-bom 작업 계정 ID"
  type        = string
}

variable "region" {
  description = "리전"
  type        = string
  default     = "ap-northeast-2"
}

variable "profile" {
  description = "re-bom 계정에 로그인된 AWS CLI 프로필 이름"
  type        = string
  default     = "rebom"
}

variable "identifier" {
  description = "RDS 인스턴스 이름"
  type        = string
  default     = "rebom-dev-pg"
}

variable "engine_version" {
  description = "PostgreSQL 메이저 버전. 부 버전은 자동 업그레이드에 맡긴다 (2026-11-09 서울 최신 18.6)"
  type        = string
  default     = "18"
}

variable "instance_class" {
  description = "인스턴스 크기. 개발용 최소 (2 vCPU 버스트, 1GB)"
  type        = string
  default     = "db.t4g.micro"
}

variable "allocated_storage_gb" {
  description = "처음 디스크 크기(gp3)"
  type        = number
  default     = 20
}

variable "max_allocated_storage_gb" {
  description = "디스크 자동 확장 상한"
  type        = number
  default     = 50
}

variable "multi_az" {
  description = "다른 가용 영역에 대기 복제본을 둘지. 켜면 인스턴스 비용이 두 배"
  type        = bool
  default     = false
}

variable "backup_retention_days" {
  description = "자동 백업 보관 일수"
  type        = number
  default     = 7
}

variable "master_username" {
  description = "관리자 사용자 이름. 비밀번호는 RDS 가 Secrets Manager 에 만들고 관리한다(테라폼 state 에 남지 않음)"
  type        = string
  default     = "rebom_admin"
}
