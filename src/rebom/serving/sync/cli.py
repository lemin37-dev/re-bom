"""S3(Silver·Gold) → RDS 동기화 실행. 경로마다 따로 돌리고, 경로 하나 = 트랜잭션 하나다.

    python -m rebom.serving.sync notice_status [--run-id <bronze_run_id>] [--dry-run]
    python -m rebom.serving.sync photo_vector  [--publish-id <id>] [--dry-run]
    python -m rebom.serving.sync tag           [--publish-id <id>] [--dry-run]   # photo_vector 를 같은 발행으로 먼저
    python -m rebom.serving.sync text_vector   [--publish-id <id>] [--dry-run]

- notice_status: Silver 공고 완료 기록(가장 늦은 run_id, 또는 --run-id)의 공고 스냅샷 + 보호소 표 → service_notice.
- photo_vector: READY 발행(가장 큰 순번, 또는 --publish-id)의 사진 스냅샷 → service_notice_photo, 그다음
  활성 image_embedding 버전의 Gold 벡터 → image_embedding.
- tag: 활성 태그 작업(photo_trait·photo_breed·text_personality·text_health 중 active_version 에 있는 것)의 Gold 태그 →
  service_notice_tag. 대표 사진은 RDS service_notice_photo 에서 고르므로 같은 발행의 photo_vector 가 먼저 끝나 있어야 한다.
- text_vector: 활성 text_embedding 버전의 Gold 글 벡터 → text_embedding.
- 활성 버전은 RDS active_version 을 시작할 때 한 번 읽고, Gold 레지스트리의 설정 해시와 같아야 쓴다. 없는 작업은 건너뛴다.
- photo_vector·text_vector 는 같은 트랜잭션에서 active_version.query_config 를 그 버전의 레지스트리 model_id·dim 으로
  채운다(검색 API 가 사용자 입력을 같은 모델·차원으로 임베딩하도록). 사람이 손으로 넣지 않는다.
- --dry-run 은 같은 계산과 쓰기를 트랜잭션 안에서 해 보고 되돌린다(숫자만 출력).
- 종료 코드: 0 성공, 4 이미 반영한 것보다 오래된 입력(StaleInput), 그 밖의 오류는 1.
- 접속: Secrets Manager rebom/dev/rebom-db. PC 에서는 SSM 포트포워딩 뒤 REBOM_DB_HOST=127.0.0.1, REBOM_DB_PORT=15432.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from typing import Any

from rebom.serving.sync import db, inputs, plan

DATA_BUCKET = "rebom-data-559987919299"
# Silver test 경로 (rescue_ingest DAG 의 silver_* task 와 같은 값). Silver prod 가 정해지면 바꾼다
NOTICE_COMPLETION_PREFIX = "bench_tmp/b-storage-test/notice_completion"
PUBLISH_PREFIX = "bench_tmp/b-storage-test/publish"
SHELTER_TABLE = "rebom_silver.silver_shelter_b_test"
PHOTO_FIELDS = ("desertion_no", "image_slot", "original_s3_key", "serving_s3_key", "thumb_s3_key", "sha256")
TAG_TASKS = ("photo_trait", "photo_breed", "text_personality", "text_health")
TEXT_TAG_TASKS = ("text_personality", "text_health")
TAG_FIELDS = ("subject_type", "subject_key", "task", "tag_code", "source", "source_hash", "searchable", "displayable",
              "rank", "similarity", "gold_run_id")


class _DryRun(Exception):
    pass


def _parse_time(text: str) -> datetime:
    at = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    if at.tzinfo is None:
        raise ValueError(f"source_fetched_at 에 시간대가 없습니다: {text!r}")
    return at


def _labels() -> dict[str, str]:
    from rebom.tagging import tag_codes, text_tags

    out = {code: t.label for code, t in tag_codes.TAGS.items()}
    for t in (*text_tags.PERSONALITY_TAGS.values(), *text_tags.HEALTH_TAGS.values()):
        out[t.code] = t.label
    return out


class Runner:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self._s3 = self._catalog = None

    @property
    def s3(self):
        if self._s3 is None:
            import boto3

            session = boto3.Session(profile_name=self.args.profile) if self.args.profile else boto3.Session()
            self._s3 = session.client("s3", region_name=os.environ.get("AWS_DEFAULT_REGION", "ap-northeast-2"))
        return self._s3

    @property
    def catalog(self):
        if self._catalog is None:
            from rebom.storage.silver.aws_catalog import get_aws_catalog

            self._catalog = get_aws_catalog(profile=self.args.profile)
        return self._catalog

    @property
    def gold_env(self):
        from rebom.common.gold_catalog import get_gold_environment

        return get_gold_environment(self.args.gold_env)

    def config(self, active: dict[str, dict[str, Any]], task: str) -> tuple[str, dict[str, Any]]:
        """활성 버전과 레지스트리 설정. RDS 의 config_hash 가 레지스트리와 다르면 멈춘다."""
        version = active[task]["version"]
        config, config_hash = inputs.registry_config(self.catalog, self.gold_env, task, version)
        if config_hash != active[task]["config_hash"]:
            raise RuntimeError(f"active_version.config_hash 가 Gold 레지스트리({self.gold_env.name})와 다릅니다: {version}")
        return version, config

    @staticmethod
    def sync_query_config(conn, active: dict[str, dict[str, Any]], task: str, version: str,
                          config: dict[str, Any]) -> dict[str, Any] | str:
        qc = plan.query_config(config)
        if qc is None:
            return f"레지스트리 {version} 에 model_id·dim 이 없어 query_config 를 채우지 못함 (벡터 검색 503)"
        changed = db.set_query_config(conn, task, version, active[task]["config_hash"], qc)
        return {**qc, "changed": changed}

    def publication(self) -> dict[str, Any]:
        return inputs.ready_publication(self.s3, self.args.bucket, self.args.publish_prefix, self.args.publish_id)

    def transaction(self, conn, route: str, pos: db.Position, work) -> dict[str, Any]:
        """sync_state 잠금 → 순번 확인 → work(conn) → sync_state 저장. --dry-run 이면 되돌린다."""
        out: dict[str, Any] = {"route": route, "dry_run": self.args.dry_run}
        try:
            with conn.transaction():
                state = db.lock_route(conn, route)
                out["rerun"] = not db.check_order(route, state, pos)
                out.update(work(conn))
                db.save_route(conn, route, pos)
                if self.args.dry_run:
                    raise _DryRun
        except _DryRun:
            pass
        return out

    # ---- 경로 ----------------------------------------------------------------------------------------------------

    def notice_status(self, conn) -> dict[str, Any]:
        a = self.args
        rec = inputs.notice_completion(self.s3, a.bucket, a.notice_completion_prefix, a.run_id)
        observed_at = _parse_time(rec["source_fetched_at"])
        silver = inputs.scan(self.catalog, rec["notice_table"], snapshot_id=rec["notice_snapshot_id"])
        shelters = inputs.shelters(self.catalog, a.shelter_table)
        rows = [plan.notice_row(r, shelters.get(str(r.get("care_reg_no") or "").strip())) for r in silver]
        pos = db.Position(silver_run_id=rec["bronze_run_id"], notice_snapshot_id=rec["notice_snapshot_id"])

        def work(c):
            p = plan.plan_notices(rows, db.existing_notices(c), observed_at)
            db.upsert_notices(c, p.upserts)
            return {"silver_run_id": rec["bronze_run_id"], "silver_rows": len(rows), "notice": p.stats}

        return self.transaction(conn, "notice_status", pos, work)

    def photo_vector(self, conn) -> dict[str, Any]:
        rec = self.publication()
        progress(f"Silver 사진 읽는 중 ({rec['photo_table']}@{rec['photo_snapshot_id']})")
        photos, present = plan.current_photos(
            inputs.scan(self.catalog, rec["photo_table"], snapshot_id=rec["photo_snapshot_id"], fields=PHOTO_FIELDS))
        progress(f"Silver 사진 {len(photos)}칸")
        active = db.active_versions(conn)
        vector = None
        if "image_embedding" in active:
            version, config = self.config(active, "image_embedding")
            in_rds = db.notice_numbers(conn)  # RDS 에 없는 공고(보호중 아님)의 사진 벡터는 읽지 않는다
            shas = {p["sha256"] for (no, _), p in photos.items() if no in in_rds and p.get("sha256")}
            progress(f"Gold 사진 벡터 읽는 중 (필요한 사진 {len(shas)}장)")
            vector = (version, inputs.image_vectors(self.catalog, self.gold_env, version, config, shas,
                                                    legacy=self.args.legacy))
        pos = db.Position(publish_seq=rec["publish_seq"], silver_run_id=rec.get("bronze_run_id"),
                          notice_snapshot_id=rec["notice_snapshot_id"], image_snapshot_id=rec["photo_snapshot_id"])

        def work(c):
            progress("RDS 사진 칸 반영 중")
            pp = plan.plan_photos(photos, present, db.notice_numbers(c), db.existing_photos(c))
            db.delete_photos(c, pp.deletes)
            db.upsert_photos(c, pp.upserts, rec["publish_seq"])
            out = {"publish_id": rec["publish_id"], "publish_seq": rec["publish_seq"], "photo": pp.stats}
            if vector is None:
                out["image_vector"] = "active_version 에 image_embedding 이 없어 건너뜀"
                return out
            version, vectors = vector
            final = {k: {**v, "original_s3_key": (photos.get(k) or {}).get("original_s3_key")}
                     for k, v in db.existing_photos(c).items()}
            out["query_config"] = self.sync_query_config(c, active, "image_embedding", version, config)
            vp = plan.plan_image_vectors(final, vectors, db.existing_image_vectors(c, version), version)
            progress(f"RDS 사진 벡터 반영 중 (넣기 {len(vp.upserts)}, 지우기 {len(vp.deletes)})")
            db.delete_image_vectors(c, vp.deletes, version)
            if not self.args.dry_run:  # dry-run 은 건수만. 벡터는 커서 RDS 로 올리는 데 오래 걸린다
                db.upsert_image_vectors(c, vp.upserts)
            out["image_vector"] = {"version": version, "gold_vectors": len(vectors), **vp.stats}
            return out

        return self.transaction(conn, "photo_vector", pos, work)

    def tag(self, conn) -> dict[str, Any]:
        from rebom.common import gold_catalog as gc

        rec = self.publication()
        active = db.active_versions(conn)
        versions: dict[str, str] = {}
        gold: list[dict[str, Any]] = []
        text_inputs: dict[tuple[str, str], str] = {}
        for task in TAG_TASKS:
            if task not in active:
                continue
            version, config = self.config(active, task)
            versions[task] = version
            gold += inputs.gold_rows(self.catalog, self.gold_env, gc.TAG_TABLE, task, version, config, TAG_FIELDS,
                                     legacy=self.args.legacy)
            if task in TEXT_TAG_TASKS:
                text_inputs.update(inputs.text_tag_inputs(self.catalog, self.gold_env, task, version, config,
                                                          legacy=self.args.legacy))
        labels = _labels()
        pos = db.Position(publish_seq=rec["publish_seq"], silver_run_id=rec.get("bronze_run_id"),
                          notice_snapshot_id=rec["notice_snapshot_id"], image_snapshot_id=rec["photo_snapshot_id"])

        def work(c):
            photo_state = db.lock_route(c, "photo_vector")
            if photo_state.get("last_publish_seq") != rec["publish_seq"]:
                raise RuntimeError(f"tag 는 대표 사진을 RDS 에서 고르므로 photo_vector 를 같은 발행(순번 {rec['publish_seq']})으로 "
                                   f"먼저 돌려야 합니다. 지금 photo_vector 순번: {photo_state.get('last_publish_seq')}")
            rep = plan.representative(db.existing_photos(c))
            tp = plan.plan_tags(db.notice_numbers(c), rep, text_inputs, gold, versions, labels, db.existing_tags(c))
            db.delete_tags(c, tp.deletes)
            db.upsert_tags(c, tp.upserts)
            return {"publish_seq": rec["publish_seq"], "versions": versions,
                    "skipped_tasks": [t for t in TAG_TASKS if t not in versions], "gold_rows": len(gold),
                    "tag": tp.stats}

        return self.transaction(conn, "tag", pos, work)

    def text_vector(self, conn) -> dict[str, Any]:
        rec = self.publication()
        active = db.active_versions(conn)
        pos = db.Position(publish_seq=rec["publish_seq"], silver_run_id=rec.get("bronze_run_id"),
                          notice_snapshot_id=rec["notice_snapshot_id"], image_snapshot_id=rec["photo_snapshot_id"])
        if "text_embedding" not in active:
            return {"route": "text_vector", "skipped": "active_version 에 text_embedding 이 없어 건너뜀"}
        version, config = self.config(active, "text_embedding")
        rows = inputs.text_vectors(self.catalog, self.gold_env, version, config, legacy=self.args.legacy)

        def work(c):
            qc = self.sync_query_config(c, active, "text_embedding", version, config)
            tp = plan.plan_text_vectors(db.notice_numbers(c), rows, db.existing_text_vectors(c, version), version)
            db.delete_text_vectors(c, tp.deletes, version)
            if not self.args.dry_run:  # dry-run 은 건수만. 벡터는 커서 RDS 로 올리는 데 오래 걸린다
                db.upsert_text_vectors(c, tp.upserts)
            return {"publish_seq": rec["publish_seq"], "version": version, "gold_rows": len(rows), "query_config": qc,
                    "text_vector": tp.stats}

        return self.transaction(conn, "text_vector", pos, work)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m rebom.serving.sync", description="S3(Silver·Gold) → RDS 동기화")
    ap.add_argument("route", choices=db.ROUTES)
    ap.add_argument("--dry-run", action="store_true", help="계산·쓰기를 해 보고 되돌린다 (사진·글 벡터는 올리지 않고 건수만)")
    ap.add_argument("--run-id", help="notice_status: Silver 공고 완료 기록의 bronze_run_id (기본: 가장 늦은 것)")
    ap.add_argument("--publish-id", help="photo_vector·tag·text_vector: READY 발행 id (기본: 가장 큰 순번)")
    ap.add_argument("--gold-env", default="test", choices=("test", "prod"), help="Gold 표 환경 (기본 test, 11/25 결정)")
    ap.add_argument("--bucket", default=os.environ.get("REBOM_S3_BUCKET", DATA_BUCKET))
    ap.add_argument("--notice-completion-prefix", default=NOTICE_COMPLETION_PREFIX)
    ap.add_argument("--publish-prefix", default=PUBLISH_PREFIX)
    ap.add_argument("--shelter-table", default=SHELTER_TABLE)
    ap.add_argument("--no-legacy", dest="legacy", action="store_false",
                    help="version 칸이 빈 옛 Gold 행을 레지스트리 설정의 옛 키로 읽지 않는다")
    ap.add_argument("--profile", default=None, help="AWS 프로필 (PC). 파드는 노드 역할")
    return ap


_T0 = time.time()


def progress(msg: str) -> None:
    """진행 상황(표준 오류). 결과 JSON 은 표준 출력이라 섞이지 않는다."""
    print(f"[{time.time() - _T0:6.1f}s] {msg}", file=sys.stderr, flush=True)


def ops_rows(args: argparse.Namespace, started_at: str, result: dict[str, Any] | None = None,
             exc: BaseException | None = None):
    """운영 기록 줄 (rds_<경로> 단계 1줄 + 실패 0~1줄). bronze_run_id 를 모르면 쓰지 않는다. --dry-run 도 쓰지 않는다."""
    from rebom.common import ops_log

    if args.dry_run:
        return [], []
    result = result or {}
    run_id = result.get("silver_run_id") or args.run_id
    publish_id = result.get("publish_id") or args.publish_id
    if not run_id and publish_id and publish_id.startswith("silver_"):
        run_id = publish_id[len("silver_"):]
    if not run_id:
        return [], []
    env, stage, at = os.environ.get("REBOM_OPS_ENV", "test"), f"rds_{args.route}", ops_log.now()
    failures = []
    if exc is None:
        status = "ok"
    else:
        if isinstance(exc, db.StaleInput):
            status, reason, blocking = "warn", "stale_input", False
        elif "config_hash" in str(exc):
            status, reason, blocking = "failed", "config_hash_mismatch", True
        else:
            status, reason, blocking = "failed", "sync_error", True
        failures.append(ops_log.failure_row(env, run_id, stage, "route", args.route, reason, observed_at=at,
                                            message=f"{type(exc).__name__}: {exc}", blocking=blocking,
                                            publish_id=publish_id))
    stats = {k: v for k, v in result.items() if k not in ("route", "dry_run")}
    stage_row = ops_log.stage_row(env, run_id, stage, status, started_at=started_at, finished_at=at,
                                  publish_id=publish_id, failed_count=len(failures), stats=stats)
    return [stage_row], failures


def _record_ops(args: argparse.Namespace, started_at: str, result=None, exc=None) -> None:
    from rebom.common import ops_log

    try:
        stages, failures = ops_rows(args, started_at, result, exc)
    except Exception as err:  # noqa: BLE001 - 운영 기록 실패로 동기화 결과를 바꾸지 않는다
        print(f"OPS_LOG_SKIPPED: {type(err).__name__}: {err}", file=sys.stderr)
        return
    if stages:
        ops_log.safe_write(stages=stages, failures=failures)


def main(argv: list[str] | None = None) -> int:
    from rebom.common.ops_log import now

    args = build_parser().parse_args(argv)
    runner = Runner(args)
    t0, started_at = time.time(), now()
    try:
        with db.connect() as conn:
            result = getattr(runner, args.route)(conn)
    except db.StaleInput as exc:
        print(f"동기화 중단: {exc}", file=sys.stderr)
        _record_ops(args, started_at, exc=exc)
        return 4
    except Exception as exc:
        _record_ops(args, started_at, exc=exc)
        raise
    result["seconds"] = round(time.time() - t0, 1)
    _record_ops(args, started_at, result=result)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
