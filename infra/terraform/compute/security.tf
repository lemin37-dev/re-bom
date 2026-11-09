# 노드 보안그룹: 바깥에서 들어오는 규칙은 하나도 없다.
# 접속은 SSM 세션·포트포워딩으로만 한다(SSH 22, k3s API 6443, Airflow 화면 모두 닫힘).
# 나중에 웹을 열 때는 80/443 을 CloudFront 관리형 프리픽스 목록에만 연다.
resource "aws_security_group" "node" {
  name        = "rebom-dev-k3s-node"
  description = "k3s nodes. No inbound from outside; access via SSM only"
  vpc_id      = local.vpc_id

  tags = { Name = "rebom-dev-k3s-node" }
}

# 노드끼리는 다 통한다(k3s API 6443, flannel VXLAN 8472/udp, kubelet 10250). 추가 노드를 붙일 때 필요하다.
resource "aws_vpc_security_group_ingress_rule" "node_self" {
  security_group_id            = aws_security_group.node.id
  referenced_security_group_id = aws_security_group.node.id
  ip_protocol                  = "-1"
  description                  = "node to node"
}

# 공공데이터 API, 사진, 패키지 설치, SSM 연결은 모두 나가는 방향이다. 응답은 보안그룹이 자동으로 들인다.
resource "aws_vpc_security_group_egress_rule" "node_all" {
  security_group_id = aws_security_group.node.id
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
  description       = "all outbound"
}
