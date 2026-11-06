# infra/terraform/network — VPC

개발 환경 VPC. 서브넷 칸은 운영(EKS)에 필요한 만큼 지금 다 만들고, 지금은 일부만 쓴다.

```
VPC 10.0.0.0/16
            가용영역 a              가용영역 c
퍼블릭     10.0.1.0/24 [EC2 k3s]   10.0.2.0/24 (비어 있음, 나중에 ALB·추가 노드)
프라이빗   10.0.11.0/24 [RDS]      10.0.12.0/24 [RDS 예비]
```

| 리소스 | 내용 |
|---|---|
| 인터넷 게이트웨이 | 퍼블릭 서브넷의 기본 경로 |
| 프라이빗 경로표 | 인터넷으로 나가는 경로 없음 (NAT 없음) |
| S3 게이트웨이 엔드포인트 | 무료. S3 트래픽이 인터넷을 거치지 않음 |
| 기본 보안그룹 | 규칙을 모두 비움 (쓰지 않음) |

NAT 를 만들지 않는 이유: EC2 는 퍼블릭 IP 로 인터넷 게이트웨이를 통해 나가므로 공공데이터 API 호출에 NAT 가 필요 없다. RDS 는 나갈 일이 없다. NAT Gateway 는 월 약 $45.

## 실행

`rebom-admin` 역할 프로필로 실행한다.

```bash
cd infra/terraform/network
cp terraform.tfvars.example terraform.tfvars
terraform init
terraform plan
terraform apply
```

상태 파일: `rebom-tfstate-559987919299` 버킷의 `network/terraform.tfstate`. `infra/terraform/compute` 가 이 출력을 읽으므로 network 를 먼저 apply 한다.

## 운영(EKS)으로 갈 때 더할 것

1. 퍼블릭 a 에 NAT Gateway (탄력적 IP 포함)
2. 프라이빗 경로표에 `0.0.0.0/0 -> NAT`
3. EKS 노드는 프라이빗 a·c, ALB 는 퍼블릭 a·c (서브넷에 `kubernetes.io/role/*` 태그가 이미 있음)

VPC, 서브넷, 대역은 바꾸지 않는다.
