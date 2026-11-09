# GitHub Actions 가 AWS 키 없이 ECR 에 이미지를 올리게 한다(OIDC).
# GitHub 에 저장하는 비밀값은 없다. 워크플로는 아래 역할 ARN 만 알면 된다(출력 github_push_role_arn).

resource "aws_iam_openid_connect_provider" "github" {
  count = var.create_github_oidc_provider ? 1 : 0

  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
}

data "aws_iam_openid_connect_provider" "github" {
  count = var.create_github_oidc_provider ? 0 : 1

  url = "https://token.actions.githubusercontent.com"
}

locals {
  github_oidc_arn = var.create_github_oidc_provider ? aws_iam_openid_connect_provider.github[0].arn : data.aws_iam_openid_connect_provider.github[0].arn
}

data "aws_iam_policy_document" "github_trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [local.github_oidc_arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    # 이 레포의 지정한 브랜치에서 돈 워크플로만. 포크·PR 에서는 못 맡는다.
    # 실제 sub (11/9 CloudTrail): repo:lemin37-dev@298081278/re-bom@1439136237:ref:refs/heads/develop (immutable subject)
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = [for ref in var.github_push_refs : "repo:${var.github_oidc_sub_repo}:ref:${ref}"]
    }
  }
}

resource "aws_iam_role" "github_push" {
  name                 = "rebom-github-ecr-push"
  description          = "GitHub Actions (re-bom develop) pushes images to ECR"
  assume_role_policy   = data.aws_iam_policy_document.github_trust.json
  max_session_duration = 3600
}

data "aws_iam_policy_document" "github_push" {
  statement {
    sid       = "EcrLogin"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  statement {
    sid = "PushToOurRepos"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:InitiateLayerUpload",
      "ecr:UploadLayerPart",
      "ecr:CompleteLayerUpload",
      "ecr:PutImage",
      "ecr:BatchGetImage",
      "ecr:GetDownloadUrlForLayer",
    ]
    resources = [for r in aws_ecr_repository.this : r.arn]
  }
}

resource "aws_iam_role_policy" "github_push" {
  name   = "rebom-github-ecr-push"
  role   = aws_iam_role.github_push.id
  policy = data.aws_iam_policy_document.github_push.json
}
