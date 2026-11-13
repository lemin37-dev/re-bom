# 켜고 끌 대상. EC2 는 infra/terraform/compute, RDS 는 infra/terraform/database 상태 파일의 출력을 읽는다.
# compute 에서 agent_count 를 바꾸면 여기도 다시 apply 해야 새 노드가 스케줄에 들어간다.
data "terraform_remote_state" "compute" {
  backend = "s3"

  config = {
    bucket  = "rebom-tfstate-559987919299"
    key     = "compute/terraform.tfstate"
    region  = "ap-northeast-2"
    profile = var.profile
  }
}

data "terraform_remote_state" "database" {
  backend = "s3"

  config = {
    bucket  = "rebom-tfstate-559987919299"
    key     = "database/terraform.tfstate"
    region  = "ap-northeast-2"
    profile = var.profile
  }
}

locals {
  server_instance_id = data.terraform_remote_state.compute.outputs.server_instance_id
  agent_instance_ids = data.terraform_remote_state.compute.outputs.agent_instance_ids
  instance_ids       = concat([local.server_instance_id], local.agent_instance_ids)
  instance_arns      = [for id in local.instance_ids : "arn:aws:ec2:${var.region}:${var.account_id}:instance/${id}"]

  db_identifier = data.terraform_remote_state.database.outputs.identifier
  db_arn        = "arn:aws:rds:${var.region}:${var.account_id}:db:${local.db_identifier}"

  # ── 시각 → cron (EventBridge Scheduler cron: 분 시 일 월 요일 연) ──
  win = { for k, w in var.windows : k => merge(w, {
    start_h  = tonumber(split(":", w.start)[0])
    start_m  = tonumber(split(":", w.start)[1])
    db_total = tonumber(split(":", w.start)[0]) * 60 + tonumber(split(":", w.start)[1]) - var.db_start_lead_minutes
    stop_h   = tonumber(split(":", w.stop)[0])
    stop_m   = tonumber(split(":", w.stop)[1])
  }) }
}
