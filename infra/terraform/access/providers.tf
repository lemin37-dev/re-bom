# 역할(IAM role)은 실제 작업 계정(re-bom)에 만든다.
provider "aws" {
  alias   = "workload"
  region  = var.workload_region
  profile = var.workload_profile

  allowed_account_ids = [var.workload_account_id]

  default_tags {
    tags = local.common_tags
  }
}

# 그룹 조회와 역할 할당(entitlement)은 Identity Delegated Admin 계정에서 한다.
# 이 계정은 us-east-1 에서만 쓸 수 있다 (프로젝트 SCP).
provider "aws" {
  alias   = "identity"
  region  = "us-east-1"
  profile = var.identity_profile

  allowed_account_ids = [var.identity_account_id]

  default_tags {
    tags = local.common_tags
  }
}

locals {
  common_tags = {
    Project   = "rebom"
    Stage     = "dev"
    Owner     = "infra"
    ManagedBy = "terraform"
  }
}
