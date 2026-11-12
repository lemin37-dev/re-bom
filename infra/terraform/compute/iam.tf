# 노드 역할. k3s 에서는 모든 파드가 이 역할을 같이 쓰므로 꼭 필요한 권한만 준다.
# 운영(EKS)에서는 파드별 역할(IRSA)로 나눈다. boto3 는 둘 다 알아서 찾으므로 코드는 그대로다.

data "aws_iam_policy_document" "ec2_trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "node" {
  name               = "rebom-dev-k3s-node"
  description        = "Re:bom dev k3s node (EC2 instance profile)"
  assume_role_policy = data.aws_iam_policy_document.ec2_trust.json
}

# SSM 세션·포트포워딩
resource "aws_iam_role_policy_attachment" "ssm_core" {
  role       = aws_iam_role.node.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

data "aws_iam_policy_document" "node" {
  # 데이터 버킷 읽기·쓰기. 지우기는 주지 않는다(수집은 덮어쓰지 않고 새 경로에 쌓는다, docs/data_contract.md 5절).
  statement {
    sid       = "DataBucketList"
    actions   = ["s3:ListBucket", "s3:GetBucketLocation"]
    resources = ["arn:aws:s3:::${var.data_bucket}"]
  }

  statement {
    sid       = "DataBucketObjects"
    actions   = ["s3:GetObject", "s3:PutObject"]
    resources = ["arn:aws:s3:::${var.data_bucket}/*"]
  }

  # API 키 등 비밀값. rebom/ 으로 시작하는 이름만 읽는다.
  statement {
    sid       = "ReadAppSecrets"
    actions   = ["secretsmanager:GetSecretValue", "secretsmanager:DescribeSecret"]
    resources = ["arn:aws:secretsmanager:${var.region}:${var.account_id}:secret:${var.secret_prefix}*"]
  }

  # 우리 ECR 저장소(rebom/...)에서 이미지 받기. 토큰 발급은 리소스를 좁힐 수 없다.
  statement {
    sid       = "EcrLogin"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  statement {
    sid       = "EcrPullOurRepos"
    actions   = ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer", "ecr:BatchCheckLayerAvailability"]
    resources = ["arn:aws:ecr:${var.region}:${var.account_id}:repository/${var.ecr_repo_prefix}*"]
  }

  # k3s 참여 토큰 (SSM SecureString, AWS 관리형 키라 kms 권한은 따로 필요 없다)
  statement {
    sid       = "ReadK3sToken"
    actions   = ["ssm:GetParameter"]
    resources = [aws_ssm_parameter.k3s_token.arn]
  }

  # Glue 읽기 권한
  statement {
    sid       = "GlueTable"
    actions   = ["glue:GetDatabase", "glue:GetTable", "glue:UpdateTable"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "node" {
  name   = "rebom-dev-k3s-node"
  role   = aws_iam_role.node.id
  policy = data.aws_iam_policy_document.node.json
}

resource "aws_iam_instance_profile" "node" {
  name = "rebom-dev-k3s-node"
  role = aws_iam_role.node.name
}
