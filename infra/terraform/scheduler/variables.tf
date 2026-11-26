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

variable "schedule_enabled" {
  description = "false 면 스케줄을 모두 멈춘다(DISABLED). 리소스는 그대로 남는다"
  type        = bool
  default     = true
}

variable "timezone" {
  description = "스케줄 시간대"
  type        = string
  default     = "Asia/Seoul"
}

variable "windows" {
  description = <<-EOT
    켜 두는 구간들. 구간마다 켜기·끄기 스케줄이 따로 생긴다 (시각은 timezone 기준 HH:MM).
    days: cron 요일 필드 (MON-FRI, *, ...)
    start: EC2 켜는 시각. RDS 는 db_start_lead_minutes 만큼 먼저 켠다
    stop: 끄기를 처음 시도하는 시각. 돌고 있는 DAG 이 있으면 stop_retry_minutes 간격으로 retry_until_hour 시대 끝까지 다시 시도한다
    retry_until_hour: 다음 구간의 RDS 켜기보다 충분히 앞이어야 한다 (RDS 가 멈추는 중이면 켜기가 실패한다)
    force_after: 이 시각 이후 회차에는 DAG 상태를 확인하지 못해도 끈다. 돌고 있는 DAG 이 확인되면 끄지 않는다
  EOT
  type = map(object({
    days             = string
    start            = string
    stop             = string
    retry_until_hour = number
    force_after      = string
  }))
  default = {
    # 03:00 수집 DAG(rescue_ingest, 매일) 용
    night = { days = "*", start = "02:40", stop = "04:00", retry_until_hour = 7, force_after = "06:00" }
    # 근무 시간
    day = { days = "MON-FRI", start = "09:00", stop = "19:00", retry_until_hour = 23, force_after = "23:00" }
  }

  validation {
    condition = alltrue(flatten([for w in values(var.windows) : [
      for t in [w.start, w.stop, w.force_after] : can(regex("^([01][0-9]|2[0-3]):[0-5][0-9]$", t))
    ]]))
    error_message = "start, stop, force_after 는 HH:MM (00:00~23:59) 이다."
  }
}

variable "db_start_lead_minutes" {
  description = "RDS 를 EC2 보다 몇 분 먼저 켤지. RDS 가 available 이 되기까지 5~10분 걸리고 Airflow 가 메타 DB 를 찾는다"
  type        = number
  default     = 15

  validation {
    condition     = var.db_start_lead_minutes >= 0 && var.db_start_lead_minutes <= 60
    error_message = "db_start_lead_minutes 는 0~60 이다."
  }
}

variable "stop_retry_minutes" {
  description = "끄기 재시도 간격(분). 60 의 약수"
  type        = number
  default     = 30

  validation {
    condition     = contains([10, 15, 20, 30, 60], var.stop_retry_minutes)
    error_message = "stop_retry_minutes 는 10, 15, 20, 30, 60 중 하나다."
  }
}

variable "airflow_namespace" {
  description = "Airflow 가 있는 네임스페이스 (infra/k8s/argocd/apps/airflow.yaml)"
  type        = string
  default     = "airflow"
}

variable "airflow_scheduler_deployment" {
  description = "Airflow 스케줄러 Deployment 이름 (Helm 릴리스 airflow)"
  type        = string
  default     = "airflow-scheduler"
}

variable "log_retention_days" {
  description = "끄기 Lambda 로그 보관 일수"
  type        = number
  default     = 30
}
