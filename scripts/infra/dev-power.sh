#!/usr/bin/env bash
# 개발 인프라 전체 끄기/켜기/상태 (k3s EC2 + RDS). dev-power.ps1 과 같은 동작 (macOS·Linux·Git Bash 용).
#   scripts/infra/dev-power.sh status|stop|start [--profile rebom] [--region ap-northeast-2] [--db rebom-dev-pg] [--yes]
# stop : 추가 노드 → 서버 → RDS, 각각 멈출 때까지 기다린다
# start: RDS(available 까지) → 서버 → 추가 노드, SSM Online 까지 기다리고 6443 포트포워딩 명령을 보여 준다
# 사람이 직접 실행한다 (AGENTS.md 5번).
set -euo pipefail

ACTION="${1:-}"; shift || true
PROFILE=rebom; REGION=ap-northeast-2; DB=""; ACCOUNT=559987919299; YES=false
while [ $# -gt 0 ]; do
  case "$1" in
    --profile) PROFILE="$2"; shift 2 ;;
    --region) REGION="$2"; shift 2 ;;
    --db) DB="$2"; shift 2 ;;
    --yes|-y) YES=true; shift ;;
    *) echo "모르는 옵션: $1" >&2; exit 2 ;;
  esac
done
case "$ACTION" in status|stop|start) ;; *) echo "사용법: $0 status|stop|start [--profile P] [--region R] [--db ID] [--yes]" >&2; exit 2 ;; esac

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
A=(--profile "$PROFILE" --region "$REGION")

aws_() {
  local out
  if ! out="$(aws "$@" "${A[@]}" 2>&1)"; then
    if echo "$out" | grep -qiE 'expired|InvalidClientTokenId|login|sso'; then
      echo "AWS 로그인이 만료된 것 같습니다. 콘솔에 rebom-admin 으로 들어간 상태에서: aws login --profile rebom-login" >&2
    fi
    echo "aws $* 실패: $out" >&2
    return 1
  fi
  printf '%s' "$out"
}

acct="$(aws_ sts get-caller-identity --query Account --output text)"
[ "$acct" = "$ACCOUNT" ] || { echo "지금 자격 증명 계정이 $acct 입니다. $ACCOUNT 가 아니라서 멈춥니다." >&2; exit 1; }

if [ -z "$DB" ]; then
  DB="$(terraform -chdir="$ROOT/infra/terraform/database" output -raw identifier 2>/dev/null || true)"
  [ -n "$DB" ] || DB=rebom-dev-pg
fi

# "ID 상태 역할 이름" 줄
nodes() {
  aws_ ec2 describe-instances \
    --filters Name=tag:Project,Values=rebom Name=tag:Stage,Values=dev Name=instance-state-name,Values=pending,running,stopping,stopped \
    --query "Reservations[].Instances[].[InstanceId,State.Name,Tags[?Key=='Role']|[0].Value,Tags[?Key=='Name']|[0].Value]" --output text
}
db_state() { aws rds describe-db-instances --db-instance-identifier "$DB" --query 'DBInstances[0].DBInstanceStatus' --output text "${A[@]}" 2>/dev/null || echo "없음"; }
ssm_ping() { aws ssm describe-instance-information --filters "Key=InstanceIds,Values=$1" --query 'InstanceInformationList[0].PingStatus' --output text "${A[@]}" 2>/dev/null || echo "?"; }

status() {
  printf '%-5s %-24s %-12s %-10s %-8s %s\n' 종류 이름 역할 상태 SSM ID
  while read -r id st role name; do
    [ -n "$id" ] || continue
    ping=-; [ "$st" = running ] && ping="$(ssm_ping "$id")"
    printf '%-5s %-24s %-12s %-10s %-8s %s\n' EC2 "$name" "$role" "$st" "$ping" "$id"
  done <<< "$(nodes)"
  printf '%-5s %-24s %-12s %-10s %-8s %s\n' RDS "$DB" postgres "$(db_state)" - "$DB"
}

confirm() { $YES && return 0; read -r -p "$1 계속할까요? (y/N) " a; [[ "$a" =~ ^[yY] ]] || { echo "취소했습니다."; exit 1; }; }

ids_by() { # $1 = server|agent, $2 = 상태 정규식
  while read -r id st role _; do
    [ -n "$id" ] || continue
    if [ "$1" = server ] && [ "$role" != k3s-server ]; then continue; fi
    if [ "$1" = agent ] && [ "$role" = k3s-server ]; then continue; fi
    [[ "$st" =~ $2 ]] && echo "$id"
  done <<< "$(nodes)"
}

stop_nodes() {
  local ids; ids="$(ids_by "$1" '^(running|pending)$' | xargs)"
  [ -n "$ids" ] || { echo "EC2 $1: 꺼져 있음 (건너뜀)"; return; }
  echo "EC2 $1 끄기: $ids"
  aws_ ec2 stop-instances --instance-ids $ids --query 'StoppingInstances[].InstanceId' --output text >/dev/null
  aws_ ec2 wait instance-stopped --instance-ids $ids
  echo "  stopped"
}

start_nodes() {
  local ids; ids="$(ids_by "$1" '^(stopped|stopping)$' | xargs)"
  [ -n "$ids" ] || { echo "EC2 $1: 켜져 있음 (건너뜀)"; return; }
  echo "EC2 $1 켜기: $ids"
  aws_ ec2 wait instance-stopped --instance-ids $ids
  aws_ ec2 start-instances --instance-ids $ids --query 'StartingInstances[].InstanceId' --output text >/dev/null
  aws_ ec2 wait instance-running --instance-ids $ids
  for id in $ids; do
    for _ in $(seq 1 40); do
      [ "$(ssm_ping "$id")" = Online ] && break
      echo "  $id SSM 연결 기다리는 중..."; sleep 15
    done
  done
  echo "  running"
}

wait_db() { # $1 = 원하는 상태
  for _ in $(seq 1 50); do
    s="$(db_state)"; [ "$s" = "$1" ] && { echo "  RDS $DB: $s"; return; }
    echo "  RDS $DB: $s ... 기다리는 중"; sleep 30
  done
  echo "RDS 가 $1 이 되지 않았습니다" >&2; exit 1
}

case "$ACTION" in
  status) status ;;
  stop)
    status; confirm "EC2(추가 노드 → 서버)와 RDS 를 끕니다."
    stop_nodes agent; stop_nodes server
    s="$(db_state)"
    case "$s" in
      available) echo "RDS $DB 끄기"; aws_ rds stop-db-instance --db-instance-identifier "$DB" --query DBInstance.DBInstanceStatus --output text >/dev/null; wait_db stopped ;;
      stopped|stopping|없음) echo "RDS $DB: $s (건너뜀)" ;;
      *) echo "RDS $DB 가 '$s' 상태라 끄지 않았습니다." >&2 ;;
    esac
    echo; echo "끝. 주의: RDS 는 꺼도 7일 뒤 AWS 가 저절로 켠다."
    ;;
  start)
    status; confirm "RDS → 서버 → 추가 노드 순서로 켭니다."
    s="$(db_state)"
    case "$s" in
      stopped) echo "RDS $DB 켜기"; aws_ rds start-db-instance --db-instance-identifier "$DB" --query DBInstance.DBInstanceStatus --output text >/dev/null; wait_db available ;;
      available) echo "RDS $DB: available (건너뜀)" ;;
      없음) echo "RDS $DB 가 없습니다. EC2 만 켭니다." >&2 ;;
      *) echo "RDS $DB 가 '$s' 입니다. available 까지 기다립니다."; wait_db available ;;
    esac
    start_nodes server; start_nodes agent
    server="$(ids_by server '^running$' | head -1)"
    echo; echo "끝. 파드가 다 뜨기까지 몇 분 더 걸린다. 퍼블릭 IP 가 바뀌어도 SSM 은 인스턴스 ID 로 붙는다."
    [ -n "$server" ] && echo "6443 포트포워딩: aws ssm start-session --profile $PROFILE --region $REGION --target $server --document-name AWS-StartPortForwardingSession --parameters portNumber=6443,localPortNumber=6443"
    ;;
esac
