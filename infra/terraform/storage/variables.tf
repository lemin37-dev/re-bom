variable "account_id" {
  description = "re-bom 작업 계정 ID. 버킷 이름 뒤에 붙여 전 세계에서 겹치지 않게 한다"
  type        = string
}

variable "region" {
  description = "버킷 리전"
  type        = string
  default     = "ap-northeast-2"
}

variable "profile" {
  description = "re-bom 계정에 로그인된 AWS CLI 프로필 이름"
  type        = string
  default     = "rebom"
}

variable "noncurrent_version_days" {
  description = "데이터 버킷에서 덮어쓰거나 지운 이전 버전을 보관할 일수"
  type        = number
  default     = 30
}
