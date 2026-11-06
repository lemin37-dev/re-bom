terraform {
  required_version = ">= 1.10.0"

  required_providers {
    aws = {
      source = "hashicorp/aws"
      # aws_accountaccess_entitlement 와 data.aws_accountaccess_application 은 6.64.0 에서 추가됨
      version = ">= 6.64.0"
    }
  }

  # 처음에는 로컬 상태 파일로 시작한다.
  # 팀원이 같이 apply 하게 되면 S3 백엔드로 옮긴다 (README 참고).
}
