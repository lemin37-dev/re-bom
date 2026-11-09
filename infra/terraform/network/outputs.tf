output "vpc_id" {
  value = aws_vpc.this.id
}

output "vpc_cidr" {
  value = aws_vpc.this.cidr_block
}

output "public_subnet_ids" {
  description = "가용 영역 끝 글자 -> 서브넷 ID"
  value       = { for k, s in aws_subnet.public : k => s.id }
}

output "private_subnet_ids" {
  description = "가용 영역 끝 글자 -> 서브넷 ID. RDS 서브넷 그룹에 둘 다 넣는다"
  value       = { for k, s in aws_subnet.private : k => s.id }
}

output "private_route_table_id" {
  description = "운영에서 NAT 경로를 더할 경로표"
  value       = aws_route_table.private.id
}
