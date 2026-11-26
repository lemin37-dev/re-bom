# infra/terraform/database — RDS PostgreSQL (개발)

서비스 DB(`rebom`, 다음에 pgvector)와 Airflow 메타 DB(`airflow`)를 한 인스턴스에 둔다.

| 리소스 | 내용 |
|---|---|
| `aws_db_instance.this` | `rebom-dev-pg`. PostgreSQL **18**(부 버전 자동 업그레이드), db.t4g.micro, gp3 20GB(자동 확장 50GB), 단일 AZ, 암호화, 공개 접속 없음 |
| `aws_db_parameter_group.this` | `rds.force_ssl = 1`(암호화 접속만), 1초 넘는 쿼리 로그 |
| `aws_db_subnet_group.this` | 프라이빗 서브넷 a·c (인터넷 경로 없음) |
| `aws_security_group.rds` | 5432 를 k3s 노드 보안그룹(compute 출력 `node_security_group_id`)에서만 받는다 |

- **버전 18 을 고른 이유**: 2026-11-09 서울 리전에서 고를 수 있는 가장 최신 메이저(18.6)이고 db.t4g.micro·gp3 를 지원한다. 지원 기간이 가장 길다. RDS 의 pgvector 확장은 18 에서도 쓸 수 있다(확장 버전은 만들 때 `SELECT * FROM pg_available_extensions WHERE name = 'vector'` 로 확인).
- **관리자 비밀번호**: `manage_master_user_password = true`. RDS 가 Secrets Manager(`rds!db-...`)에 만들고 돌리며, 테라폼 변수·state 에 남지 않는다. DB·사용자를 만들 때 한 번만 쓴다.
- **노드 역할은 관리자 시크릿을 읽지 않는다**: 모든 파드가 노드 역할을 같이 쓰므로 관리자 비밀번호를 주면 어느 파드든 DB 전체 권한을 갖게 된다. 앱은 각자의 사용자(`airflow`, `rebom`)로만 접속한다.
- **백업·유지보수 시간** (03:00 KST 수집과 안 겹치게): 백업 04:30–05:00 KST, 유지보수 월 05:30–06:00 KST. 자동 백업 7일.
- **지우기 방지**: `deletion_protection = true`, 지울 때 마지막 스냅샷(`rebom-dev-pg-final`)을 남긴다.

## 비용 (서울, 2026-11 기준 대략)

| 항목 | 월 |
|---|---|
| db.t4g.micro 단일 AZ (24시간) | 약 $19 |
| gp3 20GB | 약 $2.6 |
| 자동 백업 (DB 크기만큼은 무료) | $0 |
| 관리자 시크릿 (Secrets Manager) | $0.40 |
| Performance Insights·향상된 모니터링 | 끔 ($0) |
| **합계** | **약 $22** |

`multi_az = true` 로 바꾸면 인스턴스 비용이 두 배. RDS 는 멈춰도 7일 뒤 저절로 켜지므로 EC2 처럼 꺼 두는 절약은 어렵다.

## 실행 (사람이 직접, AGENTS.md 5번)

**`rebom-admin` 으로 한다.** `rebom-developer` 는 RDS 생성이 막혀 있다(`infra/terraform/access`). network·compute 가 먼저 apply 돼 있어야 한다(상태 파일 출력을 읽음).

```bash
cd infra/terraform/database
cp terraform.tfvars.example terraform.tfvars
terraform init
terraform plan -input=false -out=database.tfplan
terraform apply database.tfplan      # 만드는 데 10분 안팎
```

상태 파일: `rebom-tfstate-559987919299` 버킷의 `database/terraform.tfstate`.

## DB·사용자 만들기 (한 번)

내 PC 에서는 RDS 에 직접 못 붙는다(프라이빗 서브넷, 노드에서만 5432). 그래서 클러스터 안에서 psql Job 을 한 번 돌린다: `infra/k8s/db-init/job.yaml`.
PowerShell, 내 PC 에서 kubectl 이 되는 상태(SSM 6443 포트포워딩, `$env:KUBECONFIG` 확인)에서. 비밀번호는 화면에 출력하지 않는다.

```powershell
cd C:\workspace\re-bom
$env:KUBECONFIG = "$HOME\.kube\rebom-dev.yaml"; kubectl config current-context

# 1) RDS 주소와 관리자 비밀번호 (변수에만 담는다)
$ep  = terraform -chdir=infra/terraform/database output -raw endpoint
$arn = terraform -chdir=infra/terraform/database output -raw master_user_secret_arn
$m   = aws secretsmanager get-secret-value --secret-id $arn --profile rebom --query SecretString --output text | ConvertFrom-Json

# 2) 새 사용자 비밀번호 (16진수라 접속 주소에 그대로 넣을 수 있다)
$af = openssl rand -hex 24
$rb = openssl rand -hex 24

# 3) Job 이 읽을 임시 Secret → Job 실행 → 결과 확인 → 임시 Secret 삭제
kubectl create namespace airflow --dry-run=client -o yaml | kubectl apply -f -
kubectl -n airflow create secret generic db-init `
  --from-literal=PGHOST=$ep --from-literal=PGUSER=$($m.username) --from-literal=PGPASSWORD=$($m.password) `
  --from-literal=AIRFLOW_DB_PASSWORD=$af --from-literal=REBOM_DB_PASSWORD=$rb
kubectl apply -f infra/k8s/db-init/job.yaml
kubectl -n airflow wait --for=condition=complete job/db-init --timeout=180s
kubectl -n airflow logs job/db-init           # "done: airflow, rebom ..." 이면 성공
kubectl -n airflow delete job db-init
kubectl -n airflow delete secret db-init

# 4) Airflow 메타 DB 접속 Secret
kubectl -n airflow create secret generic airflow-metadata-connection `
  --from-literal=connection="postgresql://airflow:$af@${ep}:5432/airflow?sslmode=require"

# 5) 접속 정보를 Secrets Manager 에 보관 (값은 임시 파일로 넘겨 명령 인자·기록에 남기지 않는다)
#    rebom/dev/airflow-db : airflow 사용자 백업 (K8s Secret 을 잃어버렸을 때 되살리기용)
#    rebom/dev/rebom-db   : 서비스 DB. 검색 API 가 나중에 읽는다
$tmp = New-TemporaryFile
$airflowConn = "postgresql://airflow:$af@${ep}:5432/airflow?sslmode=require"
[IO.File]::WriteAllText($tmp, (@{ username = "airflow"; password = $af; host = $ep; port = 5432; dbname = "airflow"; connection = $airflowConn } | ConvertTo-Json -Compress))
aws secretsmanager create-secret --name rebom/dev/airflow-db --description "rebom dev RDS airflow user" --secret-string "file://$tmp" --profile rebom --region ap-northeast-2 --query ARN --output text
[IO.File]::WriteAllText($tmp, (@{ username = "rebom"; password = $rb; host = $ep; port = 5432; dbname = "rebom" } | ConvertTo-Json -Compress))
aws secretsmanager create-secret --name rebom/dev/rebom-db --description "rebom dev RDS rebom user (service DB)" --secret-string "file://$tmp" --profile rebom --region ap-northeast-2 --query ARN --output text
Remove-Item $tmp
Remove-Variable m, af, rb, airflowConn, tmp
```

- 두 시크릿 모두 `rebom/` 로 시작해서 **노드 역할(모든 파드)이 읽을 수 있다** (`infra/terraform/compute/iam.tf` 의 `ReadAppSecrets`). 관리자 시크릿(`rds!db-...`)은 읽을 수 없다.
- 확인은 값 없이: `aws secretsmanager describe-secret --secret-id rebom/dev/airflow-db --profile rebom --region ap-northeast-2 --query "{Name:Name,Changed:LastChangedDate}"`
- Job 은 다시 돌려도 안전하다(이미 있는 사용자·DB 는 건너뜀). 단, 사용자 비밀번호는 처음 만들 때만 정해진다. 바꾸려면 `ALTER ROLE airflow PASSWORD ...` 를 같은 방식의 Job 으로 하고, `airflow-metadata-connection` 과 `rebom/dev/airflow-db`(`put-secret-value`)도 같이 바꾼다.
- K8s Secret `airflow-metadata-connection` 을 잃어버리면 백업에서 되살린다 (서버 SSM 세션 안에서, 값이 화면에 안 나옴):

```bash
CONN=$(/snap/bin/aws secretsmanager get-secret-value --region ap-northeast-2 --secret-id rebom/dev/airflow-db \
  --query SecretString --output text | python3 -c 'import json,sys; print(json.load(sys.stdin)["connection"])')
sudo k3s kubectl -n airflow create secret generic airflow-metadata-connection --from-literal=connection="$CONN"
unset CONN
```

## 다음 단계

- pgvector: `rebom` DB 에서 `CREATE EXTENSION vector;` 는 `db/migrations/` 의 첫 마이그레이션(예: `001_init.sql`)에서 한다 (`rebom` 사용자가 DB 소유자라 만들 수 있다).
- 내 PC 에서 직접 붙어 보고 싶으면: 서버를 거치는 SSM 원격 포트포워딩(`AWS-StartPortForwardingSessionToRemoteHost`, host = 위 endpoint, portNumber 5432)을 쓴다.
- 운영(EKS)에서는 `multi_az = true`, 인스턴스 키우기, 파드별 역할(IRSA)로 시크릿 읽기.

## 주의

- `terraform destroy` 는 `deletion_protection` 때문에 실패한다. 정말 지우려면 먼저 `deletion_protection = false` 로 apply 한다. 마지막 스냅샷은 남는다(스냅샷 저장 요금 발생).
- 엔진 메이저 업그레이드(18 → 19)는 자동으로 하지 않는다(`allow_major_version_upgrade = false`). 할 때는 스냅샷을 먼저 뜬다.
