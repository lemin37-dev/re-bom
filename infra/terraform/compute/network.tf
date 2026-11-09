# VPC·서브넷은 infra/terraform/network 에서 만든다. 그 상태 파일의 출력을 읽는다.
data "terraform_remote_state" "network" {
  backend = "s3"

  config = {
    bucket  = "rebom-tfstate-559987919299"
    key     = "network/terraform.tfstate"
    region  = "ap-northeast-2"
    profile = var.profile
  }
}

locals {
  vpc_id          = data.terraform_remote_state.network.outputs.vpc_id
  public_subnets  = data.terraform_remote_state.network.outputs.public_subnet_ids
  server_subnet   = local.public_subnets["a"]
  agent_subnet_az = sort(keys(local.public_subnets)) # 추가 노드는 a, c 를 번갈아 쓴다
}
