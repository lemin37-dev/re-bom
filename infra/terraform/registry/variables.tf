variable "account_id" {
  description = "re-bom 작업 계정 ID"
  type        = string
}

variable "region" {
  description = "리전"
  type        = string
  default     = "ap-northeast-2"
}

variable "profile" {
  description = "re-bom 계정에 로그인된 AWS CLI 프로필 이름"
  type        = string
  default     = "rebom"
}

variable "repositories" {
  description = "만들 ECR 저장소 이름. 접두사 rebom/ 아래만 노드 역할이 받을 수 있다(compute iam.tf) rebom/api 는 검색 API(FastAPI, src/rebom/serving), rebom/web 은 웹 화면(Next.js, frontend/)"
  type        = list(string)
  default     = ["rebom/ingest", "rebom/api", "rebom/web"]
}

variable "github_repo" {
  description = "이미지를 올릴 수 있는 GitHub 레포 (owner/name)"
  type        = string
  default     = "lemin37-dev/re-bom"
}

variable "github_oidc_sub_repo" {
  description = "GitHub OIDC 토큰 sub 의 레포 부분. 이 레포는 immutable subject 가 켜져 있어 <owner>@<owner id>/<repo>@<repo id> 모양이다. 확인: gh api repos/lemin37-dev/re-bom/actions/oidc/customization/sub 의 sub_claim_prefix 에서 repo: 뒤. 이름이 바뀌어도 그대로라 같은 이름 레포로 가로채는 것을 막는다"
  type        = string
  default     = "lemin37-dev@298081278/re-bom@1439136237"
}

variable "github_push_refs" {
  description = "이미지를 올릴 수 있는 브랜치. 머지된 코드만 올리도록 develop 만 둔다 (PR 브랜치에서는 못 올림)"
  type        = list(string)
  default     = ["refs/heads/develop"]
}

variable "create_github_oidc_provider" {
  description = "계정에 GitHub OIDC 공급자가 아직 없으면 true. 이미 있으면 false 로 두고 기존 것을 쓴다(계정당 하나만 만들 수 있음)"
  type        = bool
  default     = true
}

variable "keep_tagged_images" {
  description = "저장소마다 남길 sha- 태그 이미지 수. 넘으면 오래된 것부터 지운다"
  type        = number
  default     = 30
}
