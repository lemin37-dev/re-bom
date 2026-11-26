data "archive_file" "stop" {
  type        = "zip"
  source_file = "${path.module}/lambda/stop_dev.py"
  output_path = "${path.module}/.build/stop_dev.zip"
}

resource "aws_cloudwatch_log_group" "stop" {
  name              = "/aws/lambda/rebom-dev-power-stop"
  retention_in_days = var.log_retention_days
}

resource "aws_lambda_function" "stop" {
  function_name = "rebom-dev-power-stop"
  description   = "돌고 있는 Airflow DAG 확인 후 dev EC2·RDS 끄기"
  role          = aws_iam_role.stop_lambda.arn

  runtime          = "python3.13"
  handler          = "stop_dev.handler"
  filename         = data.archive_file.stop.output_path
  source_code_hash = data.archive_file.stop.output_base64sha256

  timeout     = 180 # SSM 명령 결과를 최대 2분 기다린다
  memory_size = 128

  environment {
    variables = {
      SERVER_INSTANCE_ID           = local.server_instance_id
      INSTANCE_IDS                 = join(",", local.instance_ids)
      DB_IDENTIFIER                = local.db_identifier
      TIMEZONE                     = var.timezone
      AIRFLOW_NAMESPACE            = var.airflow_namespace
      AIRFLOW_SCHEDULER_DEPLOYMENT = var.airflow_scheduler_deployment
    }
  }

  depends_on = [aws_cloudwatch_log_group.stop, aws_iam_role_policy.stop_lambda]
}
