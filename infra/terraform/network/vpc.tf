# 개발: EC2(k3s)는 퍼블릭 a, RDS는 프라이빗. NAT 는 만들지 않는다(월 약 $45).
# 운영(EKS)으로 갈 때는 퍼블릭 a 에 NAT 를 더하고 private 경로표에 0.0.0.0/0 -> NAT 한 줄을 추가한다.
# VPC·서브넷·대역은 그대로 둔다.

resource "aws_vpc" "this" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = { Name = "rebom-dev" }
}

resource "aws_internet_gateway" "this" {
  vpc_id = aws_vpc.this.id

  tags = { Name = "rebom-dev" }
}

resource "aws_subnet" "public" {
  for_each = var.public_subnets

  vpc_id            = aws_vpc.this.id
  cidr_block        = each.value
  availability_zone = "${var.region}${each.key}"

  # 퍼블릭 IP 는 인스턴스에서 직접 켠다. 서브넷 기본값으로 두면 실수로 만든 리소스도 IP 를 받는다.
  map_public_ip_on_launch = false

  tags = {
    Name                     = "rebom-dev-public-${each.key}"
    Tier                     = "public"
    "kubernetes.io/role/elb" = "1" # 운영에서 ALB 가 이 서브넷을 찾는 표시
  }
}

resource "aws_subnet" "private" {
  for_each = var.private_subnets

  vpc_id            = aws_vpc.this.id
  cidr_block        = each.value
  availability_zone = "${var.region}${each.key}"

  tags = {
    Name                              = "rebom-dev-private-${each.key}"
    Tier                              = "private"
    "kubernetes.io/role/internal-elb" = "1"
  }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.this.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.this.id
  }

  tags = { Name = "rebom-dev-public" }
}

resource "aws_route_table_association" "public" {
  for_each = aws_subnet.public

  subnet_id      = each.value.id
  route_table_id = aws_route_table.public.id
}

# 인터넷으로 나가는 길이 없다. RDS 는 나갈 일이 없다.
resource "aws_route_table" "private" {
  vpc_id = aws_vpc.this.id

  tags = { Name = "rebom-dev-private" }
}

resource "aws_route_table_association" "private" {
  for_each = aws_subnet.private

  subnet_id      = each.value.id
  route_table_id = aws_route_table.private.id
}

# S3 게이트웨이 엔드포인트(무료). S3 트래픽이 인터넷을 거치지 않고, 나중에 프라이빗 노드도 NAT 없이 S3 를 쓴다.
resource "aws_vpc_endpoint" "s3" {
  vpc_id            = aws_vpc.this.id
  service_name      = "com.amazonaws.${var.region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [aws_route_table.public.id, aws_route_table.private.id]

  tags = { Name = "rebom-dev-s3" }
}

# 기본 보안그룹은 규칙을 모두 비워 둔다. 쓸 보안그룹은 compute 등에서 따로 만든다.
resource "aws_default_security_group" "this" {
  vpc_id = aws_vpc.this.id

  tags = { Name = "rebom-dev-default-unused" }
}
