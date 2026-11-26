output "admin_role_arn" {
  value = aws_iam_role.admin.arn
}

output "developer_role_arn" {
  value = aws_iam_role.developer.arn
}

output "account_access_application_arn" {
  value = data.aws_accountaccess_application.team.arn
}
