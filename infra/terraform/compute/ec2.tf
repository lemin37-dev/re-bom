# Ubuntu 24.04 (SSM 에이전트 기본 포함). AMI 가 갱신돼도 서버를 다시 만들지 않게 ignore_changes 로 묶는다.
data "aws_ssm_parameter" "ubuntu" {
  name = "/aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id"
}

locals {
  k3s_install_env = var.k3s_version != "" ? "INSTALL_K3S_VERSION=${var.k3s_version}" : "INSTALL_K3S_CHANNEL=${var.k3s_channel}"

  # 서버·추가 노드 공통: kubelet 이 ECR 이미지를 받을 때 EC2 역할로 로그인하게 한다.
  # k3s 는 아래 기본 경로에 바이너리와 설정이 있으면 kubelet 에 자동으로 넘긴다. k3s 설치 전에 깔아 둔다.
  ecr_setup = templatefile("${path.module}/scripts/ecr-credential-provider.sh.tftpl", {
    version = var.ecr_credential_provider_version
    sha256  = var.ecr_credential_provider_sha256
  })

  metadata_options = {
    http_tokens                 = "required" # IMDSv2 강제
    http_put_response_hop_limit = 2          # 파드(컨테이너)에서 노드 역할을 쓰려면 2 가 필요하다
  }
}

# 노드 1번: k3s 서버. 개발 환경은 이 한 대로 간다.
resource "aws_instance" "server" {
  ami                         = data.aws_ssm_parameter.ubuntu.value
  instance_type               = var.server_instance_type
  subnet_id                   = local.server_subnet
  vpc_security_group_ids      = [aws_security_group.node.id]
  iam_instance_profile        = aws_iam_instance_profile.node.name
  associate_public_ip_address = true # NAT 없이 인터넷 게이트웨이로 나가려면 필요. 들어오는 길은 보안그룹이 막는다

  user_data = var.install_k3s ? templatefile("${path.module}/scripts/k3s-server.sh.tftpl", {
    region          = var.region
    token_parameter = aws_ssm_parameter.k3s_token.name
    install_env     = local.k3s_install_env
    ecr_setup       = local.ecr_setup
  }) : null

  # 사용자 데이터는 처음 켤 때만 돈다. install_k3s 를 바꾸면 서버를 새로 만들어야 적용된다.
  user_data_replace_on_change = true

  metadata_options {
    http_tokens                 = local.metadata_options.http_tokens
    http_put_response_hop_limit = local.metadata_options.http_put_response_hop_limit
  }

  root_block_device {
    volume_type           = "gp3"
    volume_size           = var.server_volume_gb
    encrypted             = true
    delete_on_termination = true
  }

  tags = { Name = "rebom-dev-k3s-server", Role = "k3s-server" }

  lifecycle {
    ignore_changes = [ami]
  }
}

resource "aws_ec2_instance_state" "server" {
  instance_id = aws_instance.server.id
  state       = var.server_state
}

# 추가 노드: k3s agent. 처음 켜질 때 서버에 알아서 붙는다. agent_count 로 늘리고 줄인다.
resource "aws_instance" "agent" {
  count = var.agent_count

  ami                         = data.aws_ssm_parameter.ubuntu.value
  instance_type               = var.agent_instance_type
  subnet_id                   = local.public_subnets[local.agent_subnet_az[(count.index + 1) % length(local.agent_subnet_az)]]
  vpc_security_group_ids      = [aws_security_group.node.id]
  iam_instance_profile        = aws_iam_instance_profile.node.name
  associate_public_ip_address = true

  user_data = templatefile("${path.module}/scripts/k3s-agent.sh.tftpl", {
    region          = var.region
    token_parameter = aws_ssm_parameter.k3s_token.name
    install_env     = local.k3s_install_env
    server_url      = "https://${aws_instance.server.private_ip}:6443"
    ecr_setup       = local.ecr_setup
  })
  user_data_replace_on_change = true

  metadata_options {
    http_tokens                 = local.metadata_options.http_tokens
    http_put_response_hop_limit = local.metadata_options.http_put_response_hop_limit
  }

  root_block_device {
    volume_type           = "gp3"
    volume_size           = var.agent_volume_gb
    encrypted             = true
    delete_on_termination = true
  }

  tags = { Name = "rebom-dev-k3s-agent-${count.index + 1}", Role = "k3s-agent" }

  lifecycle {
    ignore_changes = [ami]
  }
}
