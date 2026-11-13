# ── EventBridge Scheduler 가 쓰는 역할: 켜기 API 직접 호출, 끄기 Lambda 호출 ──

data "aws_iam_policy_document" "scheduler_trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["scheduler.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [var.account_id]
    }
  }
}

resource "aws_iam_role" "scheduler" {
  name               = "rebom-dev-power-scheduler"
  description        = "Re:bom dev power schedule (EventBridge Scheduler)"
  assume_role_policy = data.aws_iam_policy_document.scheduler_trust.json
}

# EBS 는 AWS 관리형 키(aws/ebs)로 암호화돼 있다. 그 키 정책이 EC2 경유 사용을 계정 안 모두에게 허용하므로 kms 권한은 따로 필요 없다.
data "aws_iam_policy_document" "scheduler" {
  statement {
    sid       = "StartDevInstances"
    actions   = ["ec2:StartInstances"]
    resources = local.instance_arns
  }

  statement {
    sid       = "StartDevDb"
    actions   = ["rds:StartDBInstance"]
    resources = [local.db_arn]
  }

  statement {
    sid       = "InvokeStopLambda"
    actions   = ["lambda:InvokeFunction"]
    resources = [aws_lambda_function.stop.arn]
  }
}

resource "aws_iam_role_policy" "scheduler" {
  name   = "rebom-dev-power-scheduler"
  role   = aws_iam_role.scheduler.id
  policy = data.aws_iam_policy_document.scheduler.json
}

# ── 끄기 Lambda 역할 ──

data "aws_iam_policy_document" "lambda_trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "stop_lambda" {
  name               = "rebom-dev-power-stop"
  description        = "Re:bom dev stop Lambda (running DAG check, stop EC2/RDS)"
  assume_role_policy = data.aws_iam_policy_document.lambda_trust.json
}

data "aws_iam_policy_document" "stop_lambda" {
  statement {
    sid       = "Logs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.stop.arn}:*"]
  }

  # Describe 계열은 리소스를 좁힐 수 없다
  statement {
    sid       = "Describe"
    actions   = ["ec2:DescribeInstances", "ssm:DescribeInstanceInformation", "ssm:GetCommandInvocation"]
    resources = ["*"]
  }

  statement {
    sid       = "StopDevInstances"
    actions   = ["ec2:StopInstances"]
    resources = local.instance_arns
  }

  statement {
    sid       = "Db"
    actions   = ["rds:DescribeDBInstances", "rds:StopDBInstance"]
    resources = [local.db_arn]
  }

  # 서버에서 DAG 확인 명령 한 번 돌리기 (AWS-RunShellScript, 서버 인스턴스만)
  statement {
    sid     = "RunDagCheck"
    actions = ["ssm:SendCommand"]
    resources = [
      "arn:aws:ssm:${var.region}::document/AWS-RunShellScript",
      "arn:aws:ec2:${var.region}:${var.account_id}:instance/${local.server_instance_id}",
    ]
  }
}

resource "aws_iam_role_policy" "stop_lambda" {
  name   = "rebom-dev-power-stop"
  role   = aws_iam_role.stop_lambda.id
  policy = data.aws_iam_policy_document.stop_lambda.json
}
