variable "account_id" {
  description = "re-bom 작업 계정 ID"
  type        = string
}

variable "region" {
  description = "리전 (EC2·S3 가 있는 곳. CloudFront 자체는 전역)"
  type        = string
  default     = "ap-northeast-2"
}

variable "profile" {
  description = "re-bom 계정에 로그인된 AWS CLI 프로필 이름"
  type        = string
  default     = "rebom"
}

variable "price_class" {
  description = "CloudFront 엣지 범위. 한국 엣지는 PriceClass_200 부터 들어간다 (PriceClass_100 은 북미·유럽만)"
  type        = string
  default     = "PriceClass_200"

  validation {
    condition     = contains(["PriceClass_100", "PriceClass_200", "PriceClass_All"], var.price_class)
    error_message = "price_class 는 PriceClass_100, PriceClass_200, PriceClass_All 중 하나여야 한다."
  }
}

variable "origin_read_timeout" {
  description = "CloudFront 가 k3s(Traefik) 응답을 기다리는 초. 챗봇처럼 오래 걸리는 API 가 생기면 늘린다 (60 초 넘게는 한도 상향 요청 필요)"
  type        = number
  default     = 30
}
