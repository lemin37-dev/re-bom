# VPC 는 infra/terraform/network, k3s 서버·노드 보안그룹은 infra/terraform/compute,
# 데이터 버킷은 infra/terraform/storage 에서 만든다. 그 상태 파일의 출력을 읽는다.
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

data "terraform_remote_state" "storage" {
  backend = "s3"

  config = {
    bucket  = "rebom-tfstate-559987919299"
    key     = "storage/terraform.tfstate"
    region  = "ap-northeast-2"
    profile = var.profile
  }
}

# 서버의 ARN·프라이빗 DNS 이름. 프라이빗 IP 는 껐다 켜도 그대로라 origin 주소가 바뀌지 않는다.
data "aws_instance" "server" {
  instance_id = data.terraform_remote_state.compute.outputs.server_instance_id
}

locals {
  vpc_id     = data.terraform_remote_state.network.outputs.vpc_id
  node_sg_id = data.terraform_remote_state.compute.outputs.node_security_group_id

  data_bucket             = data.terraform_remote_state.storage.outputs.data_bucket
  data_bucket_domain_name = data.terraform_remote_state.storage.outputs.data_bucket_regional_domain_name
}
