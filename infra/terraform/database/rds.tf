# 개발용 PostgreSQL. 서비스 DB(rebom, 다음에 pgvector)와 Airflow 메타 DB(airflow)를 한 인스턴스에 둔다.
# 데이터베이스·사용자는 테라폼으로 만들지 않는다 (내 PC 에서 RDS 로 직접 갈 수 없음). README "DB·사용자 만들기".

resource "aws_db_parameter_group" "this" {
  name        = "${var.identifier}-pg18"
  family      = "postgres18"
  description = "rebom dev PostgreSQL 18"

  # 암호화되지 않은 접속은 받지 않는다 (PostgreSQL 15 이상 기본값이지만 명시)
  parameter {
    name  = "rds.force_ssl"
    value = "1"
  }

  # 1초 넘게 걸린 쿼리만 로그에 남긴다 (검색 API 튜닝용)
  parameter {
    name  = "log_min_duration_statement"
    value = "1000"
  }

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_db_instance" "this" {
  identifier     = var.identifier
  engine         = "postgres"
  engine_version = var.engine_version # 메이저만 고정. 부 버전은 auto_minor_version_upgrade
  instance_class = var.instance_class

  allocated_storage     = var.allocated_storage_gb
  max_allocated_storage = var.max_allocated_storage_gb
  storage_type          = "gp3"
  storage_encrypted     = true # AWS 관리형 키(aws/rds)

  username                    = var.master_username
  manage_master_user_password = true # 비밀번호는 RDS 가 Secrets Manager(rds!db-...)에 만들고 돌린다

  db_subnet_group_name   = aws_db_subnet_group.this.name
  vpc_security_group_ids = [aws_security_group.rds.id]
  publicly_accessible    = false
  multi_az               = var.multi_az
  port                   = 5432

  parameter_group_name = aws_db_parameter_group.this.name

  # 시간은 UTC. 03:00 KST 수집(18:00 UTC)과 겹치지 않게 그 뒤로 둔다
  backup_retention_period  = var.backup_retention_days
  backup_window            = "19:30-20:00"         # 04:30-05:00 KST
  maintenance_window       = "sun:20:30-sun:21:00" # 월 05:30-06:00 KST
  copy_tags_to_snapshot    = true
  delete_automated_backups = false

  auto_minor_version_upgrade  = true
  allow_major_version_upgrade = false
  apply_immediately           = false # 설정 변경은 유지보수 시간에 반영

  deletion_protection       = true
  skip_final_snapshot       = false
  final_snapshot_identifier = "${var.identifier}-final"

  # 유료 모니터링은 끈다 (기본 CloudWatch 지표만)
  performance_insights_enabled = false
  monitoring_interval          = 0

  tags = { Name = var.identifier }
}
