# scripts/infra — 개발 인프라 끄기/켜기

스케줄러가 생기기 전까지 쓰는 손 스위치. **사람이 직접 실행한다** (AGENTS.md 5번).

| 파일 | 내용 |
|---|---|
| `dev-power.ps1` | Windows PowerShell 7 용 (주로 이것) |
| `dev-power.sh` | macOS·Linux·Git Bash 용. 같은 동작 |

대상: 태그 `Project=rebom`, `Stage=dev` 인 EC2 전부(k3s 서버, 추가 노드) + RDS `rebom-dev-pg`(`infra/terraform/database` 출력 `identifier` 가 있으면 그 값).
끄지 않는 것: S3, ECR, Secrets Manager, VPC (NAT·로드밸런서·탄력적 IP 는 없다. S3 게이트웨이 엔드포인트는 무료).

## 사용법

```powershell
.\scripts\infra\dev-power.ps1 status                 # 상태만 (바꾸지 않음)
.\scripts\infra\dev-power.ps1 stop                   # 확인 질문 뒤 끄기
.\scripts\infra\dev-power.ps1 start                  # 확인 질문 뒤 켜기
.\scripts\infra\dev-power.ps1 stop -Yes              # 질문 없이
.\scripts\infra\dev-power.ps1 status -Profile rebom -Region ap-northeast-2
```

```bash
scripts/infra/dev-power.sh status|stop|start [--profile rebom] [--region ap-northeast-2] [--yes]
```

- **stop**: 추가 노드 → 서버 → RDS. 각각 멈출 때까지 기다린다 (RDS 는 5~10분)
- **start**: RDS 를 켜서 available 까지 기다린 뒤(Airflow 가 메타 DB 를 찾음) 서버 → 추가 노드. SSM 이 Online 이 될 때까지 기다리고 6443 포트포워딩 명령을 보여 준다
- 이미 원하는 상태인 것은 건너뛴다. 시작 전에 자격 증명 계정이 `559987919299` 인지 확인하고, 아니면 멈춘다
- 로그인이 만료되면: 콘솔에 rebom-admin 으로 들어간 상태에서 `aws login --profile rebom-login`

출력 예 (`status`):

```
종류 이름                 역할       상태      SSM    ID
---- ----                 ----       ----      ---    --
EC2  rebom-dev-k3s-server k3s-server running   Online i-00d51057503809599
RDS  rebom-dev-pg         postgres   available -      rebom-dev-pg
```

## 주의

- **RDS 는 꺼도 7일 뒤 AWS 가 저절로 켠다.** 오래 쉴 때는 7일 안에 다시 `stop` 한다.
- 꺼 두어도 EBS 디스크(50GB), RDS 스토리지(20GB), S3·ECR 저장 요금은 나간다.
- EC2 를 껐다 켜면 퍼블릭 IP 가 바뀐다. SSM 은 인스턴스 ID 로 붙으므로 상관없다. 켠 뒤 6443 포트포워딩은 다시 켜야 한다.
- 03:00 Airflow 수집 시간에 꺼져 있으면 그날은 건너뛰고, 다음 실행 때 마지막 성공일부터 따라잡는다.
- **테라폼과 같이 쓸 때**: compute 의 `server_state` 가 `running`/`stopped` 면 apply 때 그 상태로 되돌린다. 스크립트로 전원을 관리할 때는 `infra/terraform/compute/terraform.tfvars` 에 `server_state = "manual"` 을 두고 compute 를 한 번 apply 한다 (테라폼 상태에서 전원 리소스만 빠지고 인스턴스는 그대로). RDS 는 테라폼이 전원을 관리하지 않아 충돌이 없다.
- `dev-power.ps1` 은 PowerShell 7(`pwsh`) 기준이다. Windows PowerShell 5.1 은 BOM 없는 UTF-8 한글을 잘못 읽는다.
