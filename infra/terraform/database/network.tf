# VPC·서브넷은 infra/terraform/network, 노드 보안그룹은 infra/terraform/compute 에서 만든다. 그 상태 파일의 출력을 읽는다.
data "terraform_remote_state" "network" {
  backend = "s3"

  config = {
    bucket  = "rebom-tfstate-559987919299"
    key     = "network/terraform.tfstate"
    region  = "ap-northeast-2"
    profile = var.profile
  }
}

data "terraform_remote_state" "compute" {
  backend = "s3"

  config = {
    bucket  = "rebom-tfstate-559987919299"
    key     = "compute/terraform.tfstate"
    region  = "ap-northeast-2"
    profile = var.profile
  }
}

locals {
  vpc_id          = data.terraform_remote_state.network.outputs.vpc_id
  private_subnets = data.terraform_remote_state.network.outputs.private_subnet_ids
  node_sg_id      = data.terraform_remote_state.compute.outputs.node_security_group_id
}

# 프라이빗 a·c. 인터넷 경로가 없는 서브넷이다
resource "aws_db_subnet_group" "this" {
  name        = "${var.identifier}-private"
  description = "rebom dev RDS: private subnets a, c"
  subnet_ids  = [for az in sort(keys(local.private_subnets)) : local.private_subnets[az]]
}

# k3s 노드에서 오는 5432 만 받는다. 내 PC 에서는 직접 못 들어온다 (README "접속").
resource "aws_security_group" "rds" {
  name        = var.identifier
  description = "rebom dev RDS. 5432 from k3s nodes only"
  vpc_id      = local.vpc_id

  tags = { Name = var.identifier }
}

resource "aws_vpc_security_group_ingress_rule" "from_nodes" {
  security_group_id            = aws_security_group.rds.id
  referenced_security_group_id = local.node_sg_id
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
  description                  = "PostgreSQL from k3s nodes"
}
