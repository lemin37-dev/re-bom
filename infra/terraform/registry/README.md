# infra/terraform/registry — ECR 이미지 저장소

| 리소스 | 내용 |
|---|---|
| `aws_ecr_repository.this` | `rebom/ingest`(수집), `rebom/api`(검색 API), `rebom/web`(웹 화면) (비공개, 올릴 때 취약점 검사, 태그 덮어쓰기 허용) |
| `aws_ecr_lifecycle_policy.this` | 태그 없는 이미지 7일 뒤 삭제, `sha-` 태그는 최근 30개만 |
| `aws_iam_openid_connect_provider.github` | GitHub Actions 가 AWS 키 없이 로그인하는 공급자 (계정당 하나) |
| `aws_iam_role.github_push` | `rebom-github-ecr-push`. `lemin37-dev/re-bom` 의 `develop` 브랜치 워크플로만 맡을 수 있고, 우리 저장소에 올리기만 가능 |

태그 규칙: `develop`(가장 최근 머지, 덮어씀) + `sha-<커밋 7자리>`(기록·되돌리기용).
k3s 노드는 EC2 역할로 받는다(`infra/terraform/compute` 의 ECR 읽기 권한 + `ecr-credential-provider`). 이미지 받는 데 로그인 정보가 필요 없다.

## 실행 (사람이 직접, AGENTS.md 5번)

```bash
cd infra/terraform/registry
cp terraform.tfvars.example terraform.tfvars
terraform init
terraform plan -input=false -out=registry.tfplan
terraform apply registry.tfplan
```

- 계정에 GitHub OIDC 공급자가 이미 있으면 `create_github_oidc_provider = false` 로 둔다 (`aws iam list-open-id-connect-providers --profile rebom` 로 확인).
- 상태 파일: `rebom-tfstate-559987919299` 버킷의 `registry/terraform.tfstate`. compute 와는 따로라 순서는 상관없다.

## 적용 후

- 출력 `github_push_role_arn` 을 빌드 워크플로(`.github/workflows/`)의 `role-to-assume` 에 넣는다. 비밀값이 아니라서 워크플로 파일에 그대로 써도 된다.
- 출력 `repository_urls` 의 주소를 DAG 의 이미지로 쓴다 (`<주소>:develop`).
- `force_delete = false` 라서 이미지가 남아 있으면 `terraform destroy` 가 실패한다.
