# infra/terraform/compute — k3s 노드(EC2)

개발 환경은 EC2 **1대**(t3.xlarge)에 k3s 를 올린다. 이 폴더는 EC2, 보안그룹, EC2 역할을 만든다.
k3s 설치는 `install_k3s` 로 켠다(기본 꺼짐, Airflow 작업 때 켬).

| 리소스 | 내용 |
|---|---|
| `aws_instance.server` | 노드 1번. 퍼블릭 서브넷 a, 퍼블릭 IP 있음, Ubuntu 24.04, gp3 50GB 암호화, IMDSv2 강제 |
| `aws_ec2_instance_state.server` | 전원. `server_state = "stopped"`(기본)면 꺼 둔다 |
| `aws_security_group.node` | 바깥에서 들어오는 규칙 없음. 노드끼리만 통함. 나가는 방향 전체 허용 |
| `aws_iam_role.node` | SSM 접속, 데이터 버킷 읽기·쓰기(지우기 없음), Secrets Manager `rebom/*` 읽기, k3s 토큰 읽기, ECR `rebom/*` 이미지 받기 |
| `scripts/ecr-credential-provider.sh.tftpl` | k3s 설치 전에 kubelet ECR 로그인 플러그인(`ecr-credential-provider`, 버전·SHA-256 고정)을 깔아 둔다. 파드가 ECR 이미지를 로그인 정보 없이 받는다 |
| `aws_ssm_parameter.k3s_token` | 노드가 클러스터에 붙을 때 쓰는 토큰 (SecureString) |
| `aws_instance.agent` | 추가 노드. `agent_count`(기본 0)만큼 만들고 처음 켜질 때 서버에 알아서 붙는다 |

## 실행

`rebom-admin` 역할 프로필로 실행한다. IAM 역할을 만들기 때문에 `rebom-developer`(PowerUser)로는 안 된다.
`infra/terraform/network` 를 먼저 apply 해야 한다.

```bash
cd infra/terraform/compute
cp terraform.tfvars.example terraform.tfvars
terraform init
terraform plan
terraform apply
```

상태 파일: `rebom-tfstate-559987919299` 버킷의 `compute/terraform.tfstate`.

## 자주 바꾸는 값 (`terraform.tfvars`)

| 변수 | 기본 | 언제 바꾸나 |
|---|---|---|
| `server_state` | `stopped` | 쓸 때 `running`, 안 쓸 때 `stopped` 로 두고 apply. 꺼 두면 EC2 요금은 안 나가고 디스크 요금만 남음 |
| `install_k3s` | `false` | Airflow 작업을 시작할 때 `true` (아래 주의) |
| `server_instance_type` | `t3.xlarge` | 메모리가 모자랄 때 `t3.2xlarge`. 노드를 늘리기보다 먼저 검토 |
| `agent_count` | `0` | 노드를 더 붙일 때 1 이상. `install_k3s = true` 가 먼저 필요 |

## 접속

들어오는 포트가 없으므로 SSM 으로만 접속한다. 내 PC 에 AWS CLI 와 Session Manager 플러그인이 있어야 한다.
명령은 apply 출력 `ssm_session`, `ssm_kubectl_port_forward` 에 그대로 나온다.

```bash
# 서버에 들어가기
aws ssm start-session --profile rebom --target <server_instance_id>
sudo k3s kubectl get nodes

# 내 PC 에서 kubectl: 포트포워딩을 켜 둔 채로
aws ssm start-session --profile rebom --target <server_instance_id> \
  --document-name AWS-StartPortForwardingSession \
  --parameters portNumber=6443,localPortNumber=6443
```

서버의 `/etc/rancher/k3s/k3s.yaml` 을 내 PC kubeconfig 로 가져오고 `server: https://127.0.0.1:6443` 으로 둔다.
이 kubeconfig 는 클러스터 관리자 권한이라 팀원에게 그대로 나눠 주지 않는다.

## 주의

- **`install_k3s` 를 바꾸면 서버 EC2 가 새로 만들어진다.** 사용자 데이터는 처음 켤 때만 돌기 때문이다(`user_data_replace_on_change`). 지금처럼 서버에 아무것도 없을 때 한 번 켜고, 그 뒤로는 `scripts/k3s-server.sh.tftpl`, `scripts/ecr-credential-provider.sh.tftpl`, `ecr_credential_provider_version`·`ecr_credential_provider_sha256` 도 바꾸지 않는다(고치면 서버가 다시 만들어지고 디스크의 k3s·Airflow 데이터가 사라짐).
- `install_k3s = true` 로 처음 만들 때는 `server_state = "running"` 으로 둔다. 설치가 끝나기 전에 꺼지면 설치가 중간에 멈춘다(`/var/log/cloud-init-output.log` 로 확인).
- ECR 이미지가 안 받아지면(`ImagePullBackOff`) 서버에서 `ls /var/lib/rancher/credentialprovider/bin` 과 `sudo journalctl -u k3s | grep -i credential` 로 플러그인이 잡혔는지 본다. ECR 저장소는 `infra/terraform/registry` 에서 만든다.
- AMI 가 갱신돼도 서버를 다시 만들지 않는다(`ignore_changes = [ami]`).
- 추가 노드는 서버가 켜져 있을 때 만든다(시작할 때 서버에 붙음).
- 노드 역할은 모든 파드가 같이 쓴다. 권한을 넓히지 말고, 필요한 비밀값은 `rebom/` 이름으로 Secrets Manager 에 만든다.
- 껐다 켜면 퍼블릭 IP 가 바뀐다. IP 허용 목록을 요구하는 API 가 생기면 탄력적 IP 를 붙인다.
- RDS 보안그룹은 RDS 를 만들 때 추가한다. 출처는 출력 `node_security_group_id`, 포트 5432.

## 운영(EKS)으로 갈 때 바뀌는 것

노드는 프라이빗 서브넷 + NAT, Traefik -> ALB, 노드 역할 -> 파드별 역할(IRSA), local-path -> EBS CSI.
