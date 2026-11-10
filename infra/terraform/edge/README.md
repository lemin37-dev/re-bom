# infra/terraform/edge — CloudFront (웹 외부 공개)

CloudFront 배포 **하나**로 웹을 밖에 연다. 주소는 출력 `web_url` (`https://xxxx.cloudfront.net`).

| 경로 | origin | 캐시 |
|---|---|---|
| 기본 (`/`, 페이지) | k3s 서버 Traefik 80 → Next.js | 안 함 |
| `/api/*` | k3s 서버 Traefik 80 → (`/api` 떼고) FastAPI | 안 함 |
| `/_next/static/*` | k3s 서버 Traefik 80 → Next.js 빌드 파일 | 김 (파일 이름에 해시) |
| `/images/*` | 데이터 버킷 `images/` (OAC) | 김 (키를 덮어쓰지 않음) |

| 리소스 | 내용 |
|---|---|
| `aws_cloudfront_vpc_origin.server` | k3s 서버 EC2 를 VPC origin 으로. CloudFront 가 VPC 안 ENI 로 서버 **프라이빗 IP** 에 들어온다. 서버를 껐다 켜도 origin 주소가 그대로다 |
| `aws_vpc_security_group_ingress_rule.server_http_from_cloudfront` | 노드 보안그룹 80 을 `CloudFront-VPCOrigins-Service-SG`(AWS 가 만드는 보안그룹)에서 오는 것만 연다. 인터넷 쪽 인바운드는 여전히 없다 |
| `aws_cloudfront_origin_access_control.images` | 비공개 버킷을 CloudFront 만 서명해서 읽는다. 버킷 정책 허용 문장은 `infra/terraform/storage` |
| `aws_cloudfront_distribution.web` | 뷰어 HTTPS 강제(HTTP 는 리다이렉트), HTTP/2·3, `PriceClass_200`(한국 엣지 포함), AWS 관리형 보안 헤더 |

## 실행 (사람이 직접, AGENTS.md 5번)

`network`, `compute`, `storage` 가 먼저 있어야 한다. CloudFront·보안그룹 규칙만 만들므로 IAM 권한은 필요 없다.

```bash
cd infra/terraform/edge
cp terraform.tfvars.example terraform.tfvars
terraform init
terraform plan     # 4 to add
terraform apply    # VPC origin 이 10~15분, 배포가 5분쯤 걸린다
```

그다음 사진 경로를 연다 (storage 버킷 정책에 배포 ARN 을 넣는다):

```bash
terraform output -raw distribution_arn
cd ../storage
# terraform.tfvars 에 images_cdn_distribution_arn = "<위 값>" 추가
terraform plan     # aws_s3_bucket_policy.this["data"] 1 to change
terraform apply
```

상태 파일: `rebom-tfstate-559987919299` 버킷의 `edge/terraform.tfstate`.

## 확인

```bash
curl -I https://<배포 도메인>/                 # 200 (Next.js)
curl https://<배포 도메인>/api/health          # {"status":"ok",...}
curl -I https://<배포 도메인>/images/<desertionNo>/<번호>.webp   # 200, images/ 에 올린 뒤
```

## 주의

- **서버가 꺼져 있으면(`server_state = "stopped"`) 502/504 가 난다.** 배포는 그대로 두고 서버만 켜면 된다. CloudFront 는 꺼 둬도 요청이 없으면 요금이 거의 없다.
- AWS 문서는 VPC origin 을 프라이빗 서브넷 리소스로 설명한다. 우리 서버는 NAT 가 없어 퍼블릭 서브넷(퍼블릭 IP 있음)에 있다. 퍼블릭 서브넷 EC2 도 동작한다는 사례는 있지만, apply 에서 막히면 알려 달라 (그때는 EC2 퍼블릭 DNS 를 custom origin 으로 쓰고 탄력적 IP 를 붙이는 방식으로 바꾼다).
- 동물 사진: 공공 API 의 `popfile` 주소는 `http://` 라서 HTTPS 페이지에서 브라우저가 막거나 https 로 바꿔 요청하다 깨진다. 화면은 `/images/<desertionNo>/<번호>.webp`(이 배포의 `/images/*`)를 써야 한다. 사진을 `images/` 에 WebP 로 올리는 처리가 먼저 필요하다.
- 웹 화면은 API 를 같은 주소의 `/api` 로 부르므로 CORS 설정이 필요 없다.
- `/_next/static/*` 와 `/images/*` 는 길게 캐시한다. 같은 키로 덮어썼다면 무효화한다: `aws cloudfront create-invalidation --profile rebom --distribution-id <distribution_id> --paths "/images/*"`.
- 도메인이 생기면 us-east-1 ACM 인증서와 `aliases` 를 더한다. 지금은 `*.cloudfront.net` 기본 인증서.
- Argo CD·Airflow 화면은 이 배포에 붙이지 않는다. Traefik 호스트 규칙(`argocd.localhost`, `airflow.localhost`)으로 나뉘어 있고, 이 배포는 Host 를 origin 주소로 바꿔 보내므로(`AllViewerExceptHostHeader`) 밖에서는 닿지 않는다. 관리 화면은 `scripts/infra/dev-tunnel.ps1` (SSM 포트포워딩)로만 연다.
