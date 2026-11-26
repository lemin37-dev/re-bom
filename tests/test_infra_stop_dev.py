"""infra/terraform/scheduler/lambda/stop_dev.py 판단 로직 테스트. AWS 는 부르지 않는다 (가짜 클라이언트)."""

import datetime as dt
import importlib.util
import pathlib
import shutil
import subprocess

import pytest

LAMBDA_PATH = (
    pathlib.Path(__file__).resolve().parents[1] / "infra" / "terraform" / "scheduler" / "lambda" / "stop_dev.py"
)
KST = dt.timezone(dt.timedelta(hours=9))


class FakeEc2:
    def __init__(self, states):
        self.states = states
        self.stopped = []

    def describe_instances(self, InstanceIds):
        insts = [{"InstanceId": i, "State": {"Name": self.states[i]}} for i in InstanceIds]
        return {"Reservations": [{"Instances": insts}]}

    def stop_instances(self, InstanceIds):
        self.stopped += InstanceIds


class FakeRds:
    def __init__(self, status):
        self.status = status
        self.stopped = False

    def describe_db_instances(self, DBInstanceIdentifier):
        return {"DBInstances": [{"DBInstanceStatus": self.status}]}

    def stop_db_instance(self, DBInstanceIdentifier):
        self.stopped = True


@pytest.fixture
def mod(monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-northeast-2")
    monkeypatch.setenv("SERVER_INSTANCE_ID", "i-server")
    monkeypatch.setenv("INSTANCE_IDS", "i-server,i-agent")
    monkeypatch.setenv("DB_IDENTIFIER", "rebom-dev-pg")
    spec = importlib.util.spec_from_file_location("stop_dev", LAMBDA_PATH)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _setup(monkeypatch, mod, *, now, states=None, db="available", check=("idle", "0")):
    ec2 = FakeEc2(states or {"i-server": "running", "i-agent": "running"})
    rds = FakeRds(db)
    monkeypatch.setattr(mod, "ec2", ec2)
    monkeypatch.setattr(mod, "rds", rds)
    monkeypatch.setattr(mod, "_now", lambda: now)
    monkeypatch.setattr(mod, "_check_dags", lambda: check)
    return ec2, rds


def test_idle_stops_everything(monkeypatch, mod):
    ec2, rds = _setup(monkeypatch, mod, now=dt.datetime(2026, 11, 13, 19, 0, tzinfo=KST))
    r = mod.handler({}, None)
    assert r["decision"] == "stopped"
    assert sorted(ec2.stopped) == ["i-agent", "i-server"]
    assert rds.stopped


def test_running_dag_skips(monkeypatch, mod):
    ec2, rds = _setup(
        monkeypatch, mod, now=dt.datetime(2026, 11, 13, 19, 0, tzinfo=KST), check=("busy", "running/queued dag runs = 1")
    )
    r = mod.handler({}, None)
    assert r["decision"] == "skip: DAG running"
    assert ec2.stopped == [] and not rds.stopped


def test_check_error_skips_before_force_time(monkeypatch, mod):
    ec2, _ = _setup(
        monkeypatch, mod, now=dt.datetime(2026, 11, 13, 19, 30, tzinfo=KST), check=("unknown", "SSM command timed out")
    )
    r = mod.handler({"force_after": "23:00"}, None)
    assert r["decision"].startswith("skip: could not check")
    assert ec2.stopped == []


def test_check_error_stops_after_force_time(monkeypatch, mod):
    ec2, rds = _setup(
        monkeypatch, mod, now=dt.datetime(2026, 11, 13, 23, 0, tzinfo=KST), check=("unknown", "SSM command timed out")
    )
    r = mod.handler({"force_after": "23:00"}, None)
    assert r["decision"] == "stopped"
    assert rds.stopped


def test_night_window_force_after(monkeypatch, mod):
    # 새벽 구간: 06:00 이후엔 확인 실패여도 끈다
    ec2, _ = _setup(
        monkeypatch, mod, now=dt.datetime(2026, 11, 14, 5, 30, tzinfo=KST), check=("unknown", "k8s API not ready")
    )
    assert mod.handler({"window": "night", "force_after": "06:00"}, None)["decision"].startswith("skip")
    monkeypatch.setattr(mod, "_now", lambda: dt.datetime(2026, 11, 14, 6, 0, tzinfo=KST))
    assert mod.handler({"window": "night", "force_after": "06:00"}, None)["decision"] == "stopped"


def test_already_stopped_only_stops_db(monkeypatch, mod):
    # EC2 는 이미 꺼져 있고 RDS 만 켜져 있음 (예: 7일 뒤 자동 재시작)
    ec2, rds = _setup(
        monkeypatch,
        mod,
        now=dt.datetime(2026, 11, 13, 19, 0, tzinfo=KST),
        states={"i-server": "stopped", "i-agent": "stopped"},
        check=("busy", "should not be called"),
    )
    r = mod.handler({}, None)
    assert ec2.stopped == []
    assert rds.stopped and r["decision"] == "stopped"


class FakeSsm:
    def __init__(self, invocation, online=True):
        self.invocation = invocation
        self.online = online

    def describe_instance_information(self, Filters):
        return {"InstanceInformationList": [{"PingStatus": "Online" if self.online else "ConnectionLost"}]}

    def send_command(self, **kwargs):
        return {"Command": {"CommandId": "c-1"}}

    def get_command_invocation(self, CommandId, InstanceId):
        return self.invocation


def _ssm_check(monkeypatch, mod, status="Success", out="", online=True):
    inv = {"Status": status, "StandardOutputContent": out, "StandardErrorContent": "boom"}
    monkeypatch.setattr(mod, "ssm", FakeSsm(inv, online))
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)
    return mod._check_dags()


@pytest.mark.parametrize(
    "kwargs, verdict",
    [
        ({"out": "RUNNING_DAG_RUNS=0"}, "idle"),
        ({"out": "noise\nRUNNING_DAG_RUNS=2"}, "busy"),
        ({"out": "NO_AIRFLOW"}, "idle"),
        ({"out": "garbage"}, "unknown"),
        ({"status": "Failed"}, "unknown"),
        ({"status": "TimedOut"}, "unknown"),
        ({"online": False}, "unknown"),
    ],
)
def test_check_dags_verdicts(monkeypatch, mod, kwargs, verdict):
    assert _ssm_check(monkeypatch, mod, **kwargs)[0] == verdict


FAKE_CLUSTER = """timeout() {{ shift; "$@"; }}
kubectl() {{
  case "$*" in
    *"get --raw"*) return 0 ;;
    *"get deploy,statefulset"*)
      case "{workload}" in
        statefulset) echo statefulset.apps/airflow-scheduler ;;
        deploy) echo deployment.apps/airflow-scheduler ;;
        fail) echo "api timeout" >&2; return 1 ;;
      esac ;;
    *exec*) cat >/dev/null; echo RUNNING_DAG_RUNS=3 ;;
  esac
}}
"""


def _run_check_script(mod, tmp_path, workload):
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash 가 없다")
    script = tmp_path / "check.sh"
    script.write_text(FAKE_CLUSTER.format(workload=workload) + mod.CHECK_SCRIPT, newline="\n")
    return subprocess.run([bash, script.name], capture_output=True, text=True, cwd=tmp_path)


@pytest.mark.parametrize("workload", ["deploy", "statefulset"])
def test_check_script_finds_scheduler_as_deployment_or_statefulset(mod, tmp_path, workload):
    r = _run_check_script(mod, tmp_path, workload)
    assert r.stdout.strip().splitlines()[-1] == "RUNNING_DAG_RUNS=3", r.stderr


def test_check_script_missing_scheduler_is_no_airflow(mod, tmp_path):
    r = _run_check_script(mod, tmp_path, "")
    assert r.stdout.strip() == "NO_AIRFLOW", r.stderr


def test_check_script_lookup_error_is_not_no_airflow(mod, tmp_path):
    r = _run_check_script(mod, tmp_path, "fail")
    assert r.returncode != 0
    assert "NO_AIRFLOW" not in r.stdout


def test_dry_run_changes_nothing(monkeypatch, mod):
    ec2, rds = _setup(monkeypatch, mod, now=dt.datetime(2026, 11, 13, 19, 0, tzinfo=KST))
    r = mod.handler({"dry_run": True}, None)
    assert r["decision"].startswith("dry-run")
    assert ec2.stopped == [] and not rds.stopped
