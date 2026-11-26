# 노드가 클러스터에 붙을 때 쓰는 토큰. 서버와 추가 노드가 시작할 때 SSM 에서 읽는다.
# 값은 테라폼 상태 파일(암호화된 S3)에도 남는다. 사용자 데이터에는 넣지 않는다(개발자 역할도 볼 수 있어서).
resource "random_password" "k3s_token" {
  length  = 48
  special = false
}

resource "aws_ssm_parameter" "k3s_token" {
  name        = "/rebom/dev/k3s/token"
  description = "k3s cluster join token"
  type        = "SecureString"
  value       = random_password.k3s_token.result
}
