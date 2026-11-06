variable "workload_account_id" {
  description = "re-bom 작업 계정 ID (프로젝트 화면의 re-bom 계정)"
  type        = string
}

variable "identity_account_id" {
  description = "Identity Delegated Admin 계정 ID"
  type        = string
}

variable "workload_region" {
  description = "리소스를 만드는 기본 리전"
  type        = string
  default     = "ap-northeast-2"
}

variable "workload_profile" {
  description = "re-bom 계정에 로그인된 AWS CLI 프로필 이름"
  type        = string
  default     = "rebom"
}

variable "identity_profile" {
  description = "Identity Delegated Admin 계정에 로그인된 AWS CLI 프로필 이름"
  type        = string
  default     = "rebom-identity"
}

variable "admin_group_name" {
  description = "관리자 역할을 받을 Identity Center 그룹 이름 (콘솔에서 미리 만든다)"
  type        = string
  default     = "rebom-admins"
}

variable "developer_group_name" {
  description = "개발자 역할을 받을 Identity Center 그룹 이름 (콘솔에서 미리 만든다)"
  type        = string
  default     = "rebom-developers"
}

variable "allowed_regions" {
  description = "개발자 역할이 리소스를 만들 수 있는 리전. us-east-1 은 CloudFront 인증서(ACM) 때문에 포함"
  type        = list(string)
  default     = ["ap-northeast-2", "us-east-1"]
}

variable "allowed_instance_types" {
  description = "개발자 역할이 켤 수 있는 EC2 인스턴스 타입 패턴. GPU가 필요하면 여기에 추가"
  type        = list(string)
  default     = ["t3.*", "t3a.*", "t4g.*"]
}

variable "session_hours" {
  description = "역할 세션 시간(시간). AWS Settings 에서 쓰려면 12 여야 한다"
  type        = number
  default     = 12
}
