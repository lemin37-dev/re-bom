provider "aws" {
  region  = var.region
  profile = var.profile

  allowed_account_ids = [var.account_id]

  default_tags {
    tags = {
      Project   = "rebom"
      Stage     = "dev"
      Owner     = "infra"
      ManagedBy = "terraform"
    }
  }
}
