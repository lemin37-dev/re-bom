# 고급 기능을 켤 때 AWS가 만든 Identity Center 인스턴스와 account access manager application 을 찾는다.
data "aws_ssoadmin_instances" "team" {
  provider = aws.identity
}

locals {
  identity_center_instance_arn = tolist(data.aws_ssoadmin_instances.team.arns)[0]
  identity_store_id            = tolist(data.aws_ssoadmin_instances.team.identity_store_ids)[0]
}

data "aws_accountaccess_application" "team" {
  provider = aws.identity

  identity_center_instance_arn = local.identity_center_instance_arn
}

# 그룹은 콘솔(IAM Identity Center → 그룹)에서 만들고 사람도 거기서 넣는다.
# 여기서는 이름으로 찾아서 역할만 연결한다.
data "aws_identitystore_group" "admins" {
  provider = aws.identity

  identity_store_id = local.identity_store_id

  alternate_identifier {
    unique_attribute {
      attribute_path  = "DisplayName"
      attribute_value = var.admin_group_name
    }
  }
}

data "aws_identitystore_group" "developers" {
  provider = aws.identity

  identity_store_id = local.identity_store_id

  alternate_identifier {
    unique_attribute {
      attribute_path  = "DisplayName"
      attribute_value = var.developer_group_name
    }
  }
}

resource "aws_accountaccess_entitlement" "admins" {
  provider = aws.identity

  application_arn = data.aws_accountaccess_application.team.arn

  entitlement {
    principal_role {
      role_arn = aws_iam_role.admin.arn

      principal {
        identity_center {
          group_id = data.aws_identitystore_group.admins.group_id
        }
      }
    }
  }
}

resource "aws_accountaccess_entitlement" "developers" {
  provider = aws.identity

  application_arn = data.aws_accountaccess_application.team.arn

  entitlement {
    principal_role {
      role_arn = aws_iam_role.developer.arn

      principal {
        identity_center {
          group_id = data.aws_identitystore_group.developers.group_id
        }
      }
    }
  }
}
