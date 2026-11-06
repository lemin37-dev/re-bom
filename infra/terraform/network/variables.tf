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

variable "vpc_cidr" {
  description = "VPC 대역"
  type        = string
  default     = "10.0.0.0/16"
}

# 키(a, c)는 가용 영역 끝 글자. 운영(EKS)에서도 이 칸을 그대로 쓴다.
variable "public_subnets" {
  description = "퍼블릭 서브넷. 지금은 a 에 k3s EC2, 나중에 ALB 가 a·c 를 같이 쓴다"
  type        = map(string)
  default = {
    a = "10.0.1.0/24"
    c = "10.0.2.0/24"
  }
}

variable "private_subnets" {
  description = "프라이빗 서브넷. 지금은 RDS 용(가용 영역 2곳 필요), 운영에서는 EKS 노드도 여기에 둔다"
  type        = map(string)
  default = {
    a = "10.0.11.0/24"
    c = "10.0.12.0/24"
  }
}
