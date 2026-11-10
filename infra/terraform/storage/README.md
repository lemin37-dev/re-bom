# infra/terraform/storage — S3 버킷

버킷 하나 `rebom-data-<계정ID>` 를 경로로 나눠 쓴다.

| 경로 | 내용 | 누가 쓰나 |
|---|---|---|
| `bronze/rescue/raw/…` | 수집 원본 JSONL.gz, manifest | 수집 |
| `bronze/rescue/images/<desertionNo>/…` | 사진 원본 | 사진 수집 |
| `state/` | 수집 상태(마지막 성공일 등) | 수집 |
| `silver/` | 정제 결과 | 정제 |
| `images/<desertionNo>/<번호>.webp` | 서비스용 사진(썸네일 포함). CloudFront `/images/*` 가 이 경로만 읽음 | 사진 처리 |

설정: 공개 접근 전부 차단, ACL 끔(소유자 강제), SSE-S3 암호화, HTTPS가 아닌 요청 거부, 버전 관리(이전 버전 30일 뒤 삭제), 중단된 업로드 7일 뒤 정리.
`images/` 키는 덮어쓰지 않고 새 키로 올린다(긴 캐시 + 버전 비용 방지).
데이터 버킷의 키 규칙은 `docs/data_contract.md` 5·6절, `src/rebom/ingestion/storage.py` 를 따른다.

## 실행

```bash
cd infra/terraform/storage
cp terraform.tfvars.example terraform.tfvars
terraform init
terraform plan
terraform apply
```

상태 파일은 `rebom-tfstate-<계정ID>` 버킷의 `storage/terraform.tfstate` 에 저장된다.

## 적용 후

- 로컬 `.env` 에 `REBOM_S3_BUCKET=rebom-data-<계정ID>` (출력 `data_bucket`)
- CloudFront(OAC)의 `images/*` 읽기 허용: `infra/terraform/edge` apply 뒤 출력 `distribution_arn` 을 `terraform.tfvars` 의 `images_cdn_distribution_arn` 에 넣고 다시 apply 한다 (`AWS:SourceArn` = 배포 ARN 조건). 비워 두면 허용 문장이 없다

## 주의

- `force_destroy = false` 라서 객체가 남아 있으면 `terraform destroy` 가 실패한다. 데이터를 실수로 지우지 않게 하려는 것이다.
