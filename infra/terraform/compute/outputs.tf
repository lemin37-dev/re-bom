output "server_instance_id" {
  value = aws_instance.server.id
}

output "server_private_ip" {
  value = aws_instance.server.private_ip
}

output "agent_instance_ids" {
  value = aws_instance.agent[*].id
}

output "node_role_arn" {
  value = aws_iam_role.node.arn
}

output "node_security_group_id" {
  description = "RDS 보안그룹에서 5432 를 허용할 출처"
  value       = aws_security_group.node.id
}

output "ssm_session" {
  description = "서버에 들어가기"
  value       = "aws ssm start-session --profile ${var.profile} --region ${var.region} --target ${aws_instance.server.id}"
}

output "ssm_kubectl_port_forward" {
  description = "내 PC 에서 kubectl 을 쓰려면 이걸 켜 두고 kubeconfig 서버 주소를 https://127.0.0.1:6443 으로 둔다"
  value       = "aws ssm start-session --profile ${var.profile} --region ${var.region} --target ${aws_instance.server.id} --document-name AWS-StartPortForwardingSession --parameters portNumber=6443,localPortNumber=6443"
}
