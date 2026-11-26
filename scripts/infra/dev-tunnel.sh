#!/usr/bin/env bash
# 관리 화면(Argo CD, Airflow)과 웹을 내 PC 에서 연다. dev-tunnel.ps1 과 같은 동작 (macOS·Linux·Git Bash 용).
#   scripts/infra/dev-tunnel.sh [--port 8080] [--profile rebom] [--region ap-northeast-2]
# SSM 포트포워딩 한 번: 서버 80(Traefik) -> 내 PC 8080. 켜 둔 동안
#   http://argocd.localhost:8080  Argo CD / http://airflow.localhost:8080  Airflow / http://localhost:8080  웹
set -euo pipefail

PORT=8080; PROFILE=rebom; REGION=ap-northeast-2
while [ $# -gt 0 ]; do
  case "$1" in
    --port) PORT="$2"; shift 2 ;;
    --profile) PROFILE="$2"; shift 2 ;;
    --region) REGION="$2"; shift 2 ;;
    *) echo "모르는 옵션: $1" >&2; exit 2 ;;
  esac
done
A=(--profile "$PROFILE" --region "$REGION")

ID="$(aws ec2 describe-instances "${A[@]}" \
  --filters Name=tag:Project,Values=rebom Name=tag:Stage,Values=dev Name=tag:Role,Values=k3s-server Name=instance-state-name,Values=running \
  --query 'Reservations[0].Instances[0].InstanceId' --output text)"
if [ -z "$ID" ] || [ "$ID" = "None" ]; then
  echo "켜져 있는 k3s 서버가 없습니다. 먼저 scripts/infra/dev-power.sh start" >&2; exit 1
fi

echo "서버 $ID 의 80 -> localhost:$PORT"
echo "  Argo CD : http://argocd.localhost:$PORT"
echo "  Airflow : http://airflow.localhost:$PORT"
echo "  웹      : http://localhost:$PORT"
echo "Ctrl+C 로 끝냅니다."
exec aws ssm start-session "${A[@]}" --target "$ID" \
  --document-name AWS-StartPortForwardingSession \
  --parameters "portNumber=80,localPortNumber=$PORT"
