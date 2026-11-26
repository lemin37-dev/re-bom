# infra/terraform/scheduler — dev 환경 자동 켜기·끄기

k3s EC2(서버 + 추가 노드)와 RDS 를 정해진 시각에 켜고 끈다. CloudFront·S3·ECR 은 건드리지 않는다(켜 둔 채).
기본 구간 (Asia/Seoul), `windows` 변수로 바꾼다:

| 구간 | 요일 | 켜기 (RDS / EC2) | 끄기 시도 | 확인 실패여도 끄는 시각 |
|---|---|---|---|---|
| `night` (03:00 수집 DAG `rescue_ingest`) | 매일 | 02:25 / 02:40 | 04:00, 04:30 … 07:30 | 06:00 |
| `day` (근무 시간) | 월~금 | 08:45 / 09:00 | 19:00, 19:30 … 23:30 | 23:00 |

| 리소스 | 내용 |
|---|---|
| `aws_scheduler_schedule.db_start["<구간>"]` | RDS `StartDBInstance` 를 바로 호출. EC2 보다 `db_start_lead_minutes`(15분) 먼저 |
| `aws_scheduler_schedule.ec2_start["<구간>"]` | EC2 `StartInstances` 를 바로 호출 |
| `aws_scheduler_schedule.stop["<구간>"]` | 끄기 Lambda 호출. `stop` 부터 `stop_retry_minutes`(30분) 간격으로 `retry_until_hour` 시대 끝까지 |
| `aws_lambda_function.stop` | `lambda/stop_dev.py`. 아래 순서로 판단하고 끈다 |
| `aws_iam_role.scheduler`, `aws_iam_role.stop_lambda` | 대상 인스턴스·RDS 하나로 좁힌 권한 |

켜고 끌 대상은 `compute`·`database` 상태 파일 출력(`server_instance_id`, `agent_instance_ids`, `identifier`)에서 읽는다.
`compute` 에서 `agent_count` 를 바꾸면 이 폴더도 다시 apply 한다.

## 끄기 Lambda 판단 순서

1. 서버가 켜져 있으면 SSM Run Command 로 서버에서 스케줄러(Deployment 또는 StatefulSet `airflow-scheduler`)에
   `kubectl exec` 로 들어가 메타 DB 의 `running`·`queued` DAG run 수를 센다. 1개 이상이면 **끄지 않고** 다음 회차(30분 뒤)에 다시 본다.
   그래서 03:00 수집이 04:00 넘어 길어져도 끝난 뒤 회차에 꺼진다.
   Deployment 와 StatefulSet 둘 다 없을 때만 Airflow 가 없다고 보고 바로 끈다. 조회가 실패하면 없다고 보지 않고 3번으로 간다.
2. 확인 자체가 실패하면(SSM 오프라인, k8s API 응답 없음, 스케줄러 파드 이상) 구간의 `force_after` 전에는 끄지 않고, 그 뒤 회차에는 끈다.
   DAG 이 돌고 있다고 확인되면 마지막 회차(07:30 / 23:30)까지 끄지 않는다 → 켜진 채로 다음 구간의 끄기에서 다시 시도한다.
3. 켜져 있는 EC2 를 모두 끄고, RDS 가 `available` 이면 끈다. 이미 꺼져 있으면 아무것도 안 한다.

주의: 끄기 회차 시간대(19:00~23:30, 04:00~07:30)에 손으로 켜면(`scripts/infra/dev-power.ps1 start`) DAG 이 안 돌고 있으면 30분 안에 다시 꺼진다.
늦게까지 쓰려면 `schedule_enabled = false` 로 apply 해 스케줄을 멈춘다. 주말 낮에 손으로 켠 것은 새벽 구간의 끄기(다음 날 04:00~)가 끈다.

## 실행

`rebom-admin` 역할 프로필로 실행한다(IAM 역할을 만든다). `compute`·`database` 를 먼저 apply 해야 한다.

```bash
cd infra/terraform/scheduler
cp terraform.tfvars.example terraform.tfvars
terraform init
terraform plan
terraform apply
```

상태 파일: `rebom-tfstate-559987919299` 버킷의 `scheduler/terraform.tfstate`.

**compute 의 `server_state` 를 `manual` 로 둔다.** `running`/`stopped` 로 두면 스케줄이 바꾼 전원을 다음 compute apply 가 되돌린다.

## apply 뒤 확인

```bash
# 판단만 하고 아무것도 끄지 않는다 (DAG 확인 명령이 서버에서 제대로 도는지 본다)
aws lambda invoke --profile rebom --region ap-northeast-2 --function-name rebom-dev-power-stop --cli-binary-format raw-in-base64-out --payload '{"dry_run": true}' out.json
cat out.json
# 로그
aws logs tail /aws/lambda/rebom-dev-power-stop --profile rebom --region ap-northeast-2 --since 1h
```

## 자주 바꾸는 값 (`terraform.tfvars`)

| 변수 | 기본 | 설명 |
|---|---|---|
| `windows` | 위 표 | 구간별 `days`(cron 요일), `start`, `stop`, `retry_until_hour`, `force_after`. `retry_until_hour` 는 다음 구간 RDS 켜기보다 충분히 앞으로 (RDS 가 멈추는 중이면 켜기가 실패한다) |
| `schedule_enabled` | `true` | `false` 면 스케줄만 멈춘다(DISABLED) |
| `stop_retry_minutes` | `30` | 끄기 재시도 간격 |
| `db_start_lead_minutes` | `15` | RDS 를 EC2 보다 먼저 켜는 시간 |

## 알아 둘 것

- RDS 는 멈춘 지 **7일**이 지나면 AWS 가 자동으로 켠다. 새벽 구간이 매일 켜므로 문제없다.
  `schedule_enabled = false` 로 오래 둘 때는 7일 뒤 RDS 가 혼자 켜져 요금이 나간다(그 경우 `dev-power.ps1 stop`).
- 새벽 구간은 수집 DAG `rescue_ingest`(매일 03:00, `dags/rescue_ingest.py`)에 맞춘 것이다. DAG 시각을 바꾸면 `windows.night` 도 같이 바꾼다.
  켠 뒤 20분 안에 k3s·Airflow 가 다 떠야 03:00 실행을 놓치지 않는다. 놓치면(catchup=False) 그날은 건너뛰고, DAG 이 다음 날 마지막 성공일부터 따라잡는다.
- 켜기 순서: RDS 가 먼저 켜지지만 15분 안에 `available` 이 안 되면 Airflow 파드가 잠깐 재시작을 반복하다가 붙는다.
