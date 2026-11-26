"""Re:bom dev 환경 끄기 (EventBridge Scheduler 가 stop_time 부터 일정 간격으로 부른다).

순서
1. 서버가 켜져 있으면 SSM Run Command 로 k3s 안 Airflow 스케줄러에 들어가
   running·queued DAG run 수를 센다. 1개 이상이면 끄지 않는다(다음 회차에 다시 본다).
   확인 자체가 실패하면 force_after(이벤트, 구간마다 다름) 전에는 끄지 않고, 그 뒤에는 끈다.
2. EC2(추가 노드 포함)를 끄고, RDS 가 available 이면 끈다.

이벤트에 {"dry_run": true} 를 주면 판단만 하고 아무것도 끄지 않는다.
"""

import datetime as dt
import json
import logging
import os
import time

import boto3
from botocore.exceptions import ClientError

log = logging.getLogger()
log.setLevel(logging.INFO)

SERVER_ID = os.environ["SERVER_INSTANCE_ID"]
INSTANCE_IDS = [i for i in os.environ["INSTANCE_IDS"].split(",") if i]
DB_ID = os.environ["DB_IDENTIFIER"]
TIMEZONE = os.environ.get("TIMEZONE", "Asia/Seoul")
DEFAULT_FORCE_AFTER = "23:00"
AIRFLOW_NS = os.environ.get("AIRFLOW_NAMESPACE", "airflow")
AIRFLOW_DEPLOY = os.environ.get("AIRFLOW_SCHEDULER_DEPLOYMENT", "airflow-scheduler")

SSM_WAIT_SECONDS = 120

ec2 = boto3.client("ec2")
rds = boto3.client("rds")
ssm = boto3.client("ssm")

# 서버에서 root 로 돈다. 마지막 줄에 RUNNING_DAG_RUNS=<n> 또는 NO_AIRFLOW 를 찍는다.
CHECK_SCRIPT = f"""#!/bin/bash
set -euo pipefail
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
if ! kubectl get --raw /readyz --request-timeout=20s >/dev/null 2>&1; then
  echo "k8s API not ready" >&2; exit 2
fi
# Deployment 와 StatefulSet 을 함께 찾는다(차트가 로그 PVC 를 켜면 스케줄러가 StatefulSet 이 된다).
# 조회 자체가 실패하면 set -e 로 스크립트가 실패해 unknown 이 된다. 둘 다 없을 때만 Airflow 없음으로 본다.
target=$(kubectl -n {AIRFLOW_NS} get deploy,statefulset --field-selector metadata.name={AIRFLOW_DEPLOY} -o name --request-timeout=20s | head -n1)
if [ -z "$target" ]; then
  echo NO_AIRFLOW; exit 0
fi
timeout 90 kubectl -n {AIRFLOW_NS} exec -i "$target" -c scheduler -- python - <<'PY'
from sqlalchemy import func, select
from airflow.models.dagrun import DagRun
from airflow.utils.session import create_session

with create_session() as session:
    n = session.scalar(
        select(func.count()).select_from(DagRun).where(DagRun.state.in_(("running", "queued")))
    )
print(f"RUNNING_DAG_RUNS={{n}}")
PY
"""


def _now():
    try:
        from zoneinfo import ZoneInfo

        return dt.datetime.now(ZoneInfo(TIMEZONE))
    except Exception:  # 시간대 DB 가 없을 때: 서울 고정
        return dt.datetime.now(dt.timezone(dt.timedelta(hours=9)))


def _instances():
    res = ec2.describe_instances(InstanceIds=INSTANCE_IDS)
    return {i["InstanceId"]: i for r in res["Reservations"] for i in r["Instances"]}


def _check_dags():
    """('idle'|'busy'|'unknown', 설명)"""
    info = ssm.describe_instance_information(
        Filters=[{"Key": "InstanceIds", "Values": [SERVER_ID]}]
    )["InstanceInformationList"]
    if not info or info[0].get("PingStatus") != "Online":
        return "unknown", "SSM agent not online"

    cmd = ssm.send_command(
        InstanceIds=[SERVER_ID],
        DocumentName="AWS-RunShellScript",
        Comment="rebom dev-power: running DAG check",
        Parameters={"commands": [CHECK_SCRIPT], "executionTimeout": ["110"]},
        TimeoutSeconds=60,
    )["Command"]["CommandId"]

    deadline = time.time() + SSM_WAIT_SECONDS
    while time.time() < deadline:
        time.sleep(3)
        try:
            inv = ssm.get_command_invocation(CommandId=cmd, InstanceId=SERVER_ID)
        except ClientError as e:
            if e.response["Error"]["Code"] == "InvocationDoesNotExist":
                continue
            raise
        status = inv["Status"]
        if status in ("Pending", "InProgress", "Delayed"):
            continue
        out = inv.get("StandardOutputContent", "").strip()
        if status != "Success":
            err = inv.get("StandardErrorContent", "").strip()[-500:]
            return "unknown", f"check {status}: {err}"
        last = out.splitlines()[-1] if out else ""
        if last == "NO_AIRFLOW":
            return "idle", "airflow scheduler deployment not found"
        if last.startswith("RUNNING_DAG_RUNS="):
            n = int(last.split("=", 1)[1])
            return ("busy" if n > 0 else "idle"), f"running/queued dag runs = {n}"
        return "unknown", f"unexpected output: {out[-300:]}"
    return "unknown", "SSM command timed out"


def handler(event, context):
    event = event or {}
    dry_run = bool(event.get("dry_run"))
    force_after = event.get("force_after") or DEFAULT_FORCE_AFTER
    now = _now()
    result = {
        "time": now.isoformat(timespec="minutes"),
        "window": event.get("window"),
        "dry_run": dry_run,
        "stopped": [],
    }

    instances = _instances()
    server = instances.get(SERVER_ID)
    server_state = server["State"]["Name"] if server else "missing"
    result["server_state"] = server_state

    if server_state == "pending":
        result["decision"] = "skip: server is starting"
        log.info(json.dumps(result, ensure_ascii=False))
        return result

    if server_state == "running":
        verdict, detail = _check_dags()
        result["dag_check"] = f"{verdict}: {detail}"
        if verdict == "busy":
            result["decision"] = "skip: DAG running"
            log.info(json.dumps(result, ensure_ascii=False))
            return result
        if verdict == "unknown" and now.strftime("%H:%M") < force_after:
            result["decision"] = f"skip: could not check DAGs (force after {force_after})"
            log.warning(json.dumps(result, ensure_ascii=False))
            return result

    to_stop = [i for i, v in instances.items() if v["State"]["Name"] == "running"]
    db_state = rds.describe_db_instances(DBInstanceIdentifier=DB_ID)["DBInstances"][0]["DBInstanceStatus"]
    result["db_state"] = db_state

    if dry_run:
        result["decision"] = f"dry-run: would stop EC2 {to_stop}" + (" and RDS" if db_state == "available" else "")
        log.info(json.dumps(result, ensure_ascii=False))
        return result

    if to_stop:
        ec2.stop_instances(InstanceIds=to_stop)
        result["stopped"] += to_stop
    if db_state == "available":
        try:
            rds.stop_db_instance(DBInstanceIdentifier=DB_ID)
            result["stopped"].append(DB_ID)
        except ClientError as e:  # 막 다른 상태로 바뀐 경우. 다음 회차에 다시 본다
            result["db_error"] = str(e)

    result["decision"] = "stopped" if result["stopped"] else "nothing to stop"
    log.info(json.dumps(result, ensure_ascii=False))
    return result
