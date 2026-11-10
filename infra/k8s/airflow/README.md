# infra/k8s/airflow — Airflow (개발 k3s)

구조동물 증분 수집(`dags/rescue_ingest.py`)을 매일 03:00(Asia/Seoul)에 돌린다. 수집 자체는 `rebom-ingest` 네임스페이스에 뜨는 수집 이미지 파드가 한다.

| 파일 | 내용 |
|---|---|
| `values.yaml` | 공식 차트 `apache-airflow/airflow` 1.22.0 (Airflow 3.2.2) 값. LocalExecutor, 메타 DB 는 RDS(`infra/terraform/database`), git-sync(develop `dags/`), S3 원격 로그, 노드 `rebom/pool=main`, 리소스 제한 |
| `../argocd/apps/airflow.yaml` | Argo CD Application `airflow`. 차트 + 이 values + `infra/k8s/ingest` 를 함께 관리 (develop 기준, 자동 동기화) |
| `../ingest/` | 네임스페이스 `rebom-ingest`, Airflow 스케줄러가 거기에 파드를 띄우는 권한(Role·RoleBinding), 수집 파드용 서비스 계정 |
| `../../docker/ingest.Dockerfile` | 수집 이미지 (ECR `rebom/ingest`). `python -m rebom.ingestion.rescue_incremental` |

## 구성 요약

```
develop dags/ ──git-sync──▶ dag-processor / scheduler (airflow 네임스페이스, LocalExecutor)
                                   │ KubernetesPodOperator
                                   ▼
                         rescue-incremental 파드 (rebom-ingest 네임스페이스, rebom/ingest:develop)
                           ├─ API 키: Secret rebom-ingest (DATA_GO_KR_SERVICE_KEY)
                           └─ S3: 노드 역할 → s3://rebom-data-559987919299/bronze/..., state/...
태스크 로그 ─────────────▶ s3://rebom-data-559987919299/logs/airflow/ (노드 역할, aws_default = 키 없음)
```

- **권한(AWS)**: 노드 역할(`infra/terraform/compute/iam.tf`)에 데이터 버킷 `ListBucket`·`GetObject`·`PutObject` 가 이미 있다. Airflow S3 로그는 쓰기(Put)·읽기(Get)·목록(List)만 쓰므로 테라폼 수정은 필요 없다. 지우기(Delete)는 주지 않는다: 로그와 수집 결과 모두 덮어쓰지 않고 쌓으며, 오래된 로그 정리는 나중에 S3 수명 주기 규칙으로 한다.
- **권한(클러스터)**: 차트가 `airflow` 네임스페이스 안 권한을 만들고(`allowPodLaunching`), `rebom-ingest` 는 `infra/k8s/ingest/rbac.yaml` 이 준다. 수집 파드는 클러스터 API 를 쓰지 않아 서비스 계정 토큰을 붙이지 않는다.
- **Argo CD 와 맞추려고 한 것**: 차트가 무작위로 만드는 키(fernet·API·JWT)는 미리 만든 Secret 을 쓰고, Redis(Celery 용)는 끄고, DB 초기화 Job 은 Argo CD Sync hook 으로 돈다. 그래서 렌더링할 때마다 결과가 같다.
- 기본 관리자(admin/admin) Job 은 끈다. 관리자는 아래처럼 직접 만든다.

## 설치 순서 (사람이 직접, AGENTS.md 5번)

준비: Argo CD 설치(`infra/k8s/argocd/README.md`), 수집 이미지가 ECR 에 있음(`aws ecr list-images --repository-name rebom/ingest --profile rebom`), 내 PC 에서 kubectl 가능(SSM 6443 포트포워딩).

**kubectl 대상 확인**: 이 PC 의 기본 kubeconfig(`~/.kube/config`)는 다른 클러스터를 가리킬 수 있다. 명령 전에 `$env:KUBECONFIG = "$HOME\.kube\rebom-dev.yaml"` 를 넣고 `kubectl config current-context` 로 확인한다.
아래 명령은 PowerShell 기준이다.

### 1. Secret 만들기 (한 번)

값은 화면·명령 기록·파일에 남기지 않는다. `<...>` 는 자리표시.

Airflow 키 3개 (임의 값을 만들어 바로 넣는다):

```powershell
kubectl create namespace airflow
$fernet = python -c "import base64,os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"
kubectl -n airflow create secret generic airflow-fernet-key --from-literal=fernet-key=$fernet
kubectl -n airflow create secret generic airflow-api-secret-key --from-literal=api-secret-key=$(python -c "import secrets; print(secrets.token_hex(32))")
kubectl -n airflow create secret generic airflow-jwt-secret --from-literal=jwt-secret=$(python -c "import secrets; print(secrets.token_hex(32))")
Remove-Variable fernet
```

- fernet 키는 Airflow 연결 비밀번호 등을 암호화한다. 잃어버리면 저장된 연결을 못 읽으므로 Secret 을 지우지 않는다.

메타 DB 접속 Secret `airflow-metadata-connection`: RDS 의 airflow DB·사용자를 만들면서 같이 만든다. **`infra/terraform/database/README.md` 의 "DB·사용자 만들기"** 를 따른다 (같은 PowerShell 창에서 이어서). 그때 접속 정보가 Secrets Manager `rebom/dev/airflow-db`(키 username, password, host, port, dbname, connection)에도 백업된다. 되살리는 명령도 그 README 에 있다.

#### 수집 API 키

수집 코드는 환경변수 **`DATA_GO_KR_SERVICE_KEY`** 로 키를 읽는다(`src/rebom/ingestion/rescue_api.py`). 같은 이름을 세 곳에서 쓴다: Secrets Manager JSON 키 → K8s Secret 키 → 파드 환경변수(DAG 의 `envFrom`).

(가) 내 PC 에서 Secrets Manager `rebom/dev/data-go-kr` 만들기 (PowerShell, 한 번). 키는 화면·명령 기록·명령 인자에 남기지 않는다:

```powershell
# 둘 중 하나로 $key 를 채운다
# ① 입력받기 (화면에 안 보임)
$sec = Read-Host "DATA_GO_KR_SERVICE_KEY" -AsSecureString
$key = [Runtime.InteropServices.Marshal]::PtrToStringBSTR([Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec))
# ② 또는 레포 루트 .env 의 그 줄에서 읽기 (출력하지 않음)
# $key = ((Get-Content C:\workspace\re-bom\.env | Where-Object { $_ -match '^\s*DATA_GO_KR_SERVICE_KEY\s*=' }) -replace '^\s*DATA_GO_KR_SERVICE_KEY\s*=\s*', '').Trim().Trim('"')

$tmp = New-TemporaryFile
[IO.File]::WriteAllText($tmp, (@{ DATA_GO_KR_SERVICE_KEY = $key } | ConvertTo-Json -Compress))

# 처음 만들 때
aws secretsmanager create-secret --name rebom/dev/data-go-kr --description "rebom data.go.kr service key (ingest)" `
  --secret-string "file://$tmp" --profile rebom --region ap-northeast-2 --query ARN --output text
# 이미 있으면 (키를 바꿀 때) 위 대신
# aws secretsmanager put-secret-value --secret-id rebom/dev/data-go-kr --secret-string "file://$tmp" --profile rebom --region ap-northeast-2 --query VersionId --output text

Remove-Item $tmp
Remove-Variable key, tmp
Remove-Variable sec -ErrorAction SilentlyContinue

# 확인 (값은 안 나옴)
aws secretsmanager describe-secret --secret-id rebom/dev/data-go-kr --profile rebom --region ap-northeast-2 --query "{Name:Name,Changed:LastChangedDate}"
```

- `.env` 의 값이 URL 인코딩된 키(`%2B` 등)면 그대로 넣는다. 수집 코드가 알아서 처리한다(지금 로컬에서 쓰는 값과 같게).

(나) 서버 안에서 K8s Secret `rebom-ingest` 로 옮기기. 노드 역할이 `rebom/*` 를 읽을 수 있어 서버에서는 키 없이 읽힌다:

```bash
# 서버 SSM 세션 안에서 (aws ssm start-session --profile rebom --region ap-northeast-2 --target <server_instance_id>)
sudo k3s kubectl create namespace rebom-ingest --dry-run=client -o yaml | sudo k3s kubectl apply -f -
umask 077; F=$(mktemp -p /dev/shm)
/snap/bin/aws secretsmanager get-secret-value --region ap-northeast-2 --secret-id rebom/dev/data-go-kr \
  --query SecretString --output text \
  | python3 -c 'import json,sys; print("DATA_GO_KR_SERVICE_KEY=" + json.load(sys.stdin)["DATA_GO_KR_SERVICE_KEY"])' > "$F"
sudo k3s kubectl -n rebom-ingest create secret generic rebom-ingest --from-env-file="$F"
shred -u "$F" 2>/dev/null || rm -f "$F"
sudo k3s kubectl -n rebom-ingest get secret rebom-ingest -o jsonpath='{.data}' | python3 -c 'import json,sys; print(list(json.load(sys.stdin)))'   # ['DATA_GO_KR_SERVICE_KEY'] 만 나오면 됨
```

- 키를 바꿀 때: (가)의 `put-secret-value` → 서버에서 `sudo k3s kubectl -n rebom-ingest delete secret rebom-ingest` 후 (나)를 다시. 다음 수집 실행부터 새 키를 쓴다.
- 자동 동기화(External Secrets Operator)는 키가 늘어나면 검토.

### 2. Argo CD 에 등록

```powershell
kubectl apply -f infra/k8s/argocd/apps/airflow.yaml
kubectl -n argocd get application airflow -w      # Synced / Healthy 까지 5~10분 (이미지 받기 + DB 초기화)
kubectl -n airflow get pods                       # api-server, scheduler, dag-processor Running
```

- 이 Application 은 레포 `develop` 의 `values.yaml`·`infra/k8s/ingest` 를 읽는다. 그래서 **이 변경이 develop 에 머지된 뒤** 등록한다.
- Argo CD 를 안 쓰고 시험만 할 때 (같은 값, 사람이 실행): `helm upgrade --install airflow apache-airflow/airflow --version 1.22.0 -n airflow -f infra/k8s/airflow/values.yaml` + `kubectl apply -k infra/k8s/ingest`. 이렇게 했으면 Argo CD 에 등록하기 전에 `helm uninstall airflow -n airflow` 로 지운다 (둘이 같이 관리하면 꼬인다).

### 3. 관리자 만들기 (한 번)

```powershell
kubectl -n airflow exec -it deploy/airflow-api-server -- airflow users create --role Admin --username admin --email <이메일> --firstname <이름> --lastname <성>
```

비밀번호는 프롬프트에서 입력한다 (명령에 쓰지 않음). 팀원 계정도 같은 방법으로, 역할은 `Op` 또는 `Viewer`.

## 화면 접속

1. SSM 으로 서버 80(Traefik)을 내 PC 8080 에 붙인다 (kubectl 필요 없음, Argo CD·웹과 같은 터널)

```powershell
.\scripts\infra\dev-tunnel.ps1        # bash: scripts/infra/dev-tunnel.sh
```

2. 브라우저 `http://airflow.localhost:8080`
   - Traefik 호스트 규칙 `airflow.localhost` → `airflow-api-server` (values `ingress.apiServer`). CloudFront 는 Host 를 origin 주소로 바꿔 보내서 밖에서는 이 규칙에 닿지 않는다
   - kubectl 로 붙여도 된다: `kubectl -n airflow port-forward svc/airflow-api-server 8081:8080` → `http://localhost:8081`

## DAG 반영 방식

- `dags/` 를 고쳐 develop 에 머지하면 git-sync 가 60초마다 받아 간다. 이미지 재빌드는 필요 없다.
- 수집 코드(`src/rebom/ingestion`)를 고치면 develop 머지 때 빌드 워크플로가 `rebom/ingest:develop` 을 새로 올리고, 다음 실행부터 새 이미지를 받는다(`image_pull_policy=Always`).
- `values.yaml`·`infra/k8s/ingest` 를 고치면 Argo CD 가 develop 을 보고 반영한다. 손으로 helm upgrade 하지 않는다.

## 첫 실행 확인

1. 화면에서 `rescue_ingest` 가 보이는지 확인 (처음엔 일시정지 상태)
2. 시험 실행: DAG 를 연 채로 `--dry-run` 을 하려면 DAG 의 `arguments` 를 잠깐 `["--dry-run"]` 으로 바꾼 PR 을 쓰거나, 서버에서 직접 한 번 띄워 본다:

```bash
sudo k3s kubectl -n rebom-ingest run rescue-dry --rm -it --restart=Never \
  --image=559987919299.dkr.ecr.ap-northeast-2.amazonaws.com/rebom/ingest:develop \
  --overrides='{"spec":{"serviceAccountName":"rebom-ingest","containers":[{"name":"rescue-dry","image":"559987919299.dkr.ecr.ap-northeast-2.amazonaws.com/rebom/ingest:develop","args":["--dry-run"],"envFrom":[{"secretRef":{"name":"rebom-ingest"}}]}]}}'
```

3. 수동 실행 1회(Trigger) → 확인: 태스크 로그(S3 `logs/airflow/`), S3 `bronze/rescue/raw/dt=…/run_id=…/manifest.json`, `state/rescue_state.json` 갱신, 로그에 키·보호자 실명 없음
4. 로컬 수동 수집을 멈추고(상태 파일은 하나여야 함) DAG 일시정지 해제 → 다음날 03:00 실행 확인

## 메모리 (t3.xlarge 16GB)

| 묶음 | requests | limits |
|---|---|---|
| Airflow (scheduler 768Mi, api-server 512Mi, dag-processor 384Mi, git-sync 2×32Mi) | 약 1.7GB | 약 5.6GB |
| 수집 파드 (실행 중일 때만) | 256Mi | 1Gi |
| Argo CD | 약 0.5GB | 약 1.6GB |
| 웹 + 검색 API | 256Mi | 1Gi |
| k3s 자체(Traefik, CoreDNS 등) | 약 0.5~1GB | — |
| 합계 | 약 3~3.5GB | 약 9GB |

limits 를 다 써도 16GB 안이다. 임베딩 배치가 들어오면 그때 `kubectl top pods -A` 로 실제 사용량을 보고 다시 나눈다.

## 되돌리기

- 설정 되돌리기: develop 에서 해당 커밋을 revert 하는 PR → 머지되면 Argo CD 가 맞춘다.
- DAG 만 잠깐 멈추기: 화면에서 일시정지 (상태 파일은 그대로).
- 전부 내리기 (사람이 실행): `kubectl -n argocd delete application airflow` → Airflow 와 `rebom-ingest` 권한이 지워진다. RDS 의 airflow DB 와 직접 만든 Secret 은 남는다.

## 주의

- **상태 파일은 하나만**: 전환 뒤 로컬에서 같은 버킷으로 수집을 돌리면 마지막 성공일·보호중 목록이 서로 덮인다. 로컬은 `--dry-run` 또는 다른 `REBOM_S3_PREFIX` 로만.
- **호출 한도**: 하루 10,000건. 수집기 자체 상한 600회. 재시도 1회 + 수동 실행을 여러 번 해도 한도 안인지 본다.
- 서버를 밤에 끄는 스케줄을 넣으면 03:00 실행과 겹치지 않게 한다.
