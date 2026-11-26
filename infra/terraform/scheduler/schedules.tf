resource "aws_scheduler_schedule_group" "this" {
  name = "rebom-dev-power"
}

locals {
  schedule_state = var.schedule_enabled ? "ENABLED" : "DISABLED"
}

# 구간(var.windows)마다 RDS 켜기, EC2 켜기, 끄기 스케줄을 하나씩 만든다.

# ── 켜기: Lambda 없이 AWS API 를 바로 부른다 (universal target) ──
# 이미 켜져 있으면 RDS 는 InvalidDBInstanceState 오류가 나지만 아무 영향 없다. 재시도는 하지 않는다.

resource "aws_scheduler_schedule" "db_start" {
  for_each = local.win

  name        = "rebom-dev-${each.key}-db-start"
  group_name  = aws_scheduler_schedule_group.this.name
  description = "[${each.key}] RDS ${local.db_identifier} 켜기 (EC2 ${each.value.start} 보다 ${var.db_start_lead_minutes}분 먼저)"
  state       = local.schedule_state

  schedule_expression          = "cron(${each.value.db_total % 60} ${floor(each.value.db_total / 60)} ? * ${each.value.days} *)"
  schedule_expression_timezone = var.timezone

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = "arn:aws:scheduler:::aws-sdk:rds:startDBInstance"
    role_arn = aws_iam_role.scheduler.arn
    input    = jsonencode({ DbInstanceIdentifier = local.db_identifier })

    retry_policy {
      maximum_retry_attempts = 0
    }
  }

  lifecycle {
    precondition {
      condition     = each.value.db_total >= 0
      error_message = "start 에서 db_start_lead_minutes 를 빼면 전날로 넘어간다. start 를 늦추거나 lead 를 줄인다."
    }
  }
}

resource "aws_scheduler_schedule" "ec2_start" {
  for_each = local.win

  name        = "rebom-dev-${each.key}-ec2-start"
  group_name  = aws_scheduler_schedule_group.this.name
  description = "[${each.key}] k3s EC2 켜기 ${each.value.start}"
  state       = local.schedule_state

  schedule_expression          = "cron(${each.value.start_m} ${each.value.start_h} ? * ${each.value.days} *)"
  schedule_expression_timezone = var.timezone

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = "arn:aws:scheduler:::aws-sdk:ec2:startInstances"
    role_arn = aws_iam_role.scheduler.arn
    input    = jsonencode({ InstanceIds = local.instance_ids })

    retry_policy {
      maximum_retry_attempts = 2
    }
  }
}

# ── 끄기: Lambda 가 돌고 있는 DAG 을 확인한 뒤 끈다 ──
# stop 부터 stop_retry_minutes 간격으로 retry_until_hour 시대 끝까지. 이미 꺼져 있으면 Lambda 가 아무것도 안 한다.

resource "aws_scheduler_schedule" "stop" {
  for_each = local.win

  name        = "rebom-dev-${each.key}-stop"
  group_name  = aws_scheduler_schedule_group.this.name
  description = "[${each.key}] k3s EC2·RDS 끄기 시도 (${each.value.stop}부터 ${var.stop_retry_minutes}분 간격, ${each.value.retry_until_hour}시대까지)"
  state       = local.schedule_state

  schedule_expression = format(
    "cron(%d/%d %s ? * %s *)",
    each.value.stop_m,
    var.stop_retry_minutes,
    each.value.retry_until_hour == each.value.stop_h ? "${each.value.stop_h}" : "${each.value.stop_h}-${each.value.retry_until_hour}",
    each.value.days,
  )
  schedule_expression_timezone = var.timezone

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.stop.arn
    role_arn = aws_iam_role.scheduler.arn
    input    = jsonencode({ window = each.key, force_after = each.value.force_after })

    retry_policy {
      maximum_retry_attempts = 0 # 다음 회차가 곧 다시 시도한다
    }
  }

  lifecycle {
    precondition {
      condition     = each.value.stop_m < var.stop_retry_minutes
      error_message = "stop 의 분은 stop_retry_minutes 보다 작아야 한다 (예: 30분 간격이면 HH:00~HH:29)."
    }
    precondition {
      condition     = each.value.retry_until_hour >= each.value.stop_h && each.value.retry_until_hour <= 23
      error_message = "retry_until_hour 는 stop 의 시 이상, 23 이하다 (자정을 넘기지 않는다)."
    }
  }
}
