# 수집 등 우리 이미지 저장소. 비공개이고, k3s 노드는 EC2 역할로 받는다(compute 의 ecr-credential-provider).
# 태그: develop(최신, 덮어씀) + sha-<커밋>(기록용). develop 을 덮어써야 해서 MUTABLE 이다.
resource "aws_ecr_repository" "this" {
  for_each = toset(var.repositories)

  name                 = each.value
  image_tag_mutability = "MUTABLE"
  force_delete         = false

  image_scanning_configuration {
    scan_on_push = true # 올릴 때마다 기본 취약점 검사(무료)
  }

  encryption_configuration {
    encryption_type = "AES256"
  }
}

resource "aws_ecr_lifecycle_policy" "this" {
  for_each = aws_ecr_repository.this

  repository = each.value.name
  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "태그 없는 이미지(develop 을 덮어써서 떨어진 것)는 7일 뒤 삭제"
        selection = {
          tagStatus   = "untagged"
          countType   = "sinceImagePushed"
          countUnit   = "days"
          countNumber = 7
        }
        action = { type = "expire" }
      },
      {
        rulePriority = 2
        description  = "sha- 태그는 최근 것만 남김"
        selection = {
          tagStatus     = "tagged"
          tagPrefixList = ["sha-"]
          countType     = "imageCountMoreThan"
          countNumber   = var.keep_tagged_images
        }
        action = { type = "expire" }
      },
    ]
  })
}
