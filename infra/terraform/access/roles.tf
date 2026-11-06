# Account access manager 가 역할을 맡을 수 있게 하는 신뢰 정책.
# sts:TagSession 이 없으면 자격 증명 발급이 실패한다 (provider 문서).
# aws:SourceArn 조건으로 우리 팀 application 에서 온 요청만 허용한다.
data "aws_iam_policy_document" "account_access_trust" {
  provider = aws.workload

  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole", "sts:SetContext", "sts:TagSession"]

    principals {
      type        = "Service"
      identifiers = ["account-access.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceArn"
      values   = [data.aws_accountaccess_application.team.arn]
    }
  }
}

# 관리자: 전체 권한. IAM 역할 생성, CloudFront·OAC 설정 등 인프라 작업용.
resource "aws_iam_role" "admin" {
  provider = aws.workload

  name                 = "rebom-admin"
  description          = "Re:bom admins via account access manager"
  assume_role_policy   = data.aws_iam_policy_document.account_access_trust.json
  max_session_duration = var.session_hours * 3600
}

resource "aws_iam_role_policy_attachment" "admin" {
  provider = aws.workload

  role       = aws_iam_role.admin.name
  policy_arn = "arn:aws:iam::aws:policy/AdministratorAccess"
}

# 개발자: PowerUserAccess(IAM 사용자·역할 관리 불가) + 사고 방지 제한.
resource "aws_iam_role" "developer" {
  provider = aws.workload

  name                 = "rebom-developer"
  description          = "Re:bom developers via account access manager"
  assume_role_policy   = data.aws_iam_policy_document.account_access_trust.json
  max_session_duration = var.session_hours * 3600
}

resource "aws_iam_role_policy_attachment" "developer_power_user" {
  provider = aws.workload

  role       = aws_iam_role.developer.name
  policy_arn = "arn:aws:iam::aws:policy/PowerUserAccess"
}

data "aws_iam_policy_document" "developer_guardrails" {
  provider = aws.workload

  # 허용 리전 밖에서는 아무것도 못 만든다 (전역 서비스는 예외).
  statement {
    sid    = "OnlyAllowedRegions"
    effect = "Deny"
    not_actions = [
      "iam:*", "sts:*", "sso:*", "organizations:*", "account:*",
      "cloudfront:*", "route53:*", "budgets:*", "ce:*",
      "support:*", "health:*", "pricing:*",
    ]
    resources = ["*"]

    condition {
      test     = "StringNotEquals"
      variable = "aws:RequestedRegion"
      values   = var.allowed_regions
    }
  }

  # 작은 인스턴스만 켤 수 있다.
  statement {
    sid       = "OnlySmallInstances"
    effect    = "Deny"
    actions   = ["ec2:RunInstances"]
    resources = ["arn:aws:ec2:*:*:instance/*"]

    condition {
      test     = "StringNotLike"
      variable = "ec2:InstanceType"
      values   = var.allowed_instance_types
    }
  }

  # 켜두기만 해도 비싼 리소스는 관리자만 만든다.
  statement {
    sid    = "NoExpensiveAlwaysOn"
    effect = "Deny"
    actions = [
      "ec2:CreateNatGateway",
      "rds:CreateDBInstance",
      "rds:CreateDBCluster",
      "eks:CreateCluster",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "developer_guardrails" {
  provider = aws.workload

  name   = "rebom-developer-guardrails"
  role   = aws_iam_role.developer.id
  policy = data.aws_iam_policy_document.developer_guardrails.json
}
