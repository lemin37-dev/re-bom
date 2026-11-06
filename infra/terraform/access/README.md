# infra/terraform/access — 팀원 AWS 권한

Identity Center 그룹에 re-bom 계정의 IAM 역할을 연결한다 (account access manager).

| 그룹 (콘솔에서 생성) | 역할 (테라폼) | 권한 |
|---|---|---|
| `rebom-admins` | `rebom-admin` | AdministratorAccess |
| `rebom-developers` | `rebom-developer` | PowerUserAccess + 제한 (서울·us-east-1만, t3/t3a/t4g만, NAT·RDS·EKS 생성 금지) |

세션은 12시간 (AWS Settings 에서 역할을 쓰려면 12시간이어야 함).

## 콘솔에서 먼저 할 일 (한 번)

1. Identity Delegated Admin 계정 콘솔, 리전 **us-east-1** → IAM Identity Center → 그룹
2. `rebom-admins`, `rebom-developers` 그룹을 만들고 팀원을 넣는다
3. `559987919299`로 시작하는 기본 그룹에는 lemin 만 남긴다 (테라폼이 실패해도 들어갈 수 있는 비상 경로)

사람 추가·제거는 계속 콘솔 그룹에서 한다. 테라폼은 그룹 ↔ 역할 연결만 관리한다.

## 실행

AWS CLI 프로필 두 개가 필요하다: `rebom` (re-bom 계정), `rebom-identity` (Identity Delegated Admin 계정, us-east-1).

```bash
cd infra/terraform/access
cp terraform.tfvars.example terraform.tfvars
terraform init
terraform plan
terraform apply
```

AWS provider 6.64.0 이상이 필요하다 (`aws_accountaccess_entitlement`).

## 적용 후 확인

- 개발자 그룹 팀원이 account access portal 에서 re-bom 계정의 `rebom-developer` 역할로 들어가지는지
- 그 역할로 서울에 `t3.micro` 는 만들어지고 `m5.large` 는 막히는지

## 되돌리기

`terraform destroy` 하면 역할과 연결만 지워진다. 그룹과 사람은 콘솔에 그대로 남는다.

## 상태 파일

처음에는 로컬(`terraform.tfstate`, 커밋 금지)로 시작한다. 여러 명이 apply 하게 되면 S3 버킷을 만들어 `backend "s3"` (`use_lockfile = true`)로 옮긴다.
