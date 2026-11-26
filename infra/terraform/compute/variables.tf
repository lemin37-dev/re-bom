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

variable "server_instance_type" {
  description = "k3s 서버(노드 1번) 타입. Airflow·FastAPI·임베딩 배치를 같이 돌린다. 모자라면 t3.2xlarge 로 키운다"
  type        = string
  default     = "t3.xlarge"
}

variable "server_volume_gb" {
  description = "서버 루트 디스크(gp3) 크기. 컨테이너 이미지·모델 가중치 캐시·Airflow 메타 DB 가 여기에 쌓인다"
  type        = number
  default     = 50
}

variable "server_state" {
  description = "서버 전원. running | stopped | manual. running/stopped 는 apply 때 그 상태로 맞춘다(쓰지 않을 때 stopped 면 EC2 요금이 안 나감, 디스크 요금은 남음). manual 은 테라폼이 전원을 건드리지 않는다: scripts/infra/dev-power.ps1 로 손으로 끄고 켤 때 쓴다"
  type        = string
  default     = "stopped"

  validation {
    condition     = contains(["running", "stopped", "manual"], var.server_state)
    error_message = "server_state 는 running, stopped, manual 중 하나여야 한다."
  }
}

variable "install_k3s" {
  description = "true 면 서버가 처음 켜질 때 k3s 서버를 설치한다. 바꾸면 서버 EC2 가 새로 만들어진다(아래 README 주의)"
  type        = bool
  default     = false
}

variable "k3s_channel" {
  description = "k3s 설치 채널. k3s_version 을 주면 그 버전이 우선한다"
  type        = string
  default     = "stable"
}

variable "k3s_version" {
  description = "고정할 k3s 버전(예: v1.33.5+k3s1). 비우면 k3s_channel 의 최신"
  type        = string
  default     = ""
}

variable "agent_count" {
  description = "추가 노드(k3s agent) 수. 필요할 때만 1 이상으로 올린다. install_k3s = true 일 때만 쓸 수 있다"
  type        = number
  default     = 0

  validation {
    condition     = var.agent_count >= 0 && var.agent_count <= 3
    error_message = "agent_count 는 0~3 이다."
  }

  validation {
    condition     = var.agent_count == 0 || var.install_k3s
    error_message = "추가 노드는 서버에 k3s 가 있어야 붙는다. install_k3s = true 로 먼저 서버를 만든다."
  }
}

variable "agent_instance_type" {
  description = "추가 노드 타입"
  type        = string
  default     = "t3.large"
}

variable "agent_volume_gb" {
  description = "추가 노드 루트 디스크 크기"
  type        = number
  default     = 30
}

variable "data_bucket" {
  description = "노드가 읽고 쓰는 데이터 버킷 (infra/terraform/storage 출력 data_bucket)"
  type        = string
  default     = "rebom-data-559987919299"
}

variable "secret_prefix" {
  description = "노드가 읽을 수 있는 Secrets Manager 이름 접두사. API 키는 rebom/... 이름으로 만든다"
  type        = string
  default     = "rebom/"
}

variable "ecr_repo_prefix" {
  description = "노드가 이미지를 받을 수 있는 ECR 저장소 이름 접두사 (infra/terraform/registry)"
  type        = string
  default     = "rebom/"
}

variable "ecr_credential_provider_version" {
  description = "k3s 가 ECR 로그인에 쓰는 kubelet 플러그인 버전 (kubernetes/cloud-provider-aws)"
  type        = string
  default     = "v1.37.0"
}

variable "ecr_credential_provider_sha256" {
  description = "위 버전 linux/amd64 바이너리의 SHA-256. 버전을 바꾸면 같이 바꾼다(공식 .sha256 파일 값)"
  type        = string
  default     = "842e0fd8159f5ed8df2f38e6521e1e4f0a1ee80cf1e1ac119bbf20bf9c20681c"
}
