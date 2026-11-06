terraform {
  backend "s3" {
    bucket       = "rebom-tfstate-559987919299"
    key          = "storage/terraform.tfstate"
    region       = "ap-northeast-2"
    profile      = "rebom"
    use_lockfile = true
    encrypt      = true
  }
}
