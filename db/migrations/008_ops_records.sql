-- 008_ops_records.sql — 운영 기록 표 3개 (rebom DB, rebom 사용자로 실행)
-- 운영 모니터링 화면(/ops) 의 숫자·실패 원인·스키마 지문 비교 출처. 서비스 화면은 읽지 않는다.
--   ops_run_summary     실행 1번의 단계 1개 = 1행 (단계가 끝날 때, 또는 요약 태스크가 모아서)
--   ops_failure         실패한 대상 1개 = 1행 (Bronze·사진·Silver·RDS 동기화. Gold 실패는 Gold 로그를 바로 읽음)
--   ops_schema_snapshot Silver·Gold Iceberg 표의 실제 칼럼을 관측한 기록 (바뀌었을 때만 1행)
-- 기록 쓰기가 실패해도 본 작업은 멈추지 않는다(src/rebom/common/ops_log.py). 그래서 이 파일을 적용하기 전에도
-- 파이프라인은 그대로 돈다. 값 목록은 DBML 에서 Enum, 여기서는 text + CHECK (docs/rds_schema.dbml, data_contract 16.3).
-- 보호자 실명·연락처는 evidence 에도 넣지 않는다.

CREATE TABLE IF NOT EXISTS ops_run_summary (
    id             bigserial    PRIMARY KEY,
    env            text         NOT NULL CHECK (env IN ('test', 'prod')),
    bronze_run_id  text         NOT NULL,
    publish_id     text,
    gold_run_id    text,
    stage          text         NOT NULL CHECK (stage ~ '^(bronze|photo|silver_notice|silver_photo|silver_publish|gold_[a-z_]+|rds_[a-z_]+)$'),
    status         text         NOT NULL CHECK (status IN ('ok', 'warn', 'failed', 'skipped')),
    started_at     timestamptz  NOT NULL,
    finished_at    timestamptz,
    input_count    integer,
    output_count   integer,
    failed_count   integer,
    stats          jsonb        NOT NULL DEFAULT '{}'::jsonb,
    code_version   text,
    created_at     timestamptz  NOT NULL DEFAULT now(),
    CONSTRAINT ops_run_summary_run_stage UNIQUE (env, bronze_run_id, stage)
);

CREATE TABLE IF NOT EXISTS ops_failure (
    id             bigserial    PRIMARY KEY,
    env            text         NOT NULL CHECK (env IN ('test', 'prod')),
    bronze_run_id  text         NOT NULL,
    publish_id     text,
    stage          text         NOT NULL CHECK (stage ~ '^(bronze|photo|silver_notice|silver_photo|silver_publish|gold_[a-z_]+|rds_[a-z_]+)$'),
    subject_type   text         NOT NULL CHECK (subject_type IN ('animal', 'photo_slot', 'partition', 'route', 'run')),
    subject_key    text         NOT NULL,
    reason_code    text         NOT NULL CHECK (reason_code IN (
                                    'aborted', 'count_mismatch', 'dropped_row', 'not_found', 'replaced_not_found', 'gave_up',
                                    'download_failed', 'raw_content_conflict', 'silver_transform_conflict',
                                    'status_history_conflict', 'partition_incomplete', 'publish_blocked', 'stale_input',
                                    'config_hash_mismatch', 'no_active_version', 'fk_skipped', 'sync_error')),
    message        text,
    attempts       integer,
    evidence       jsonb        NOT NULL DEFAULT '{}'::jsonb,
    evidence_ref   text,
    blocking       boolean      NOT NULL DEFAULT false,
    observed_at    timestamptz  NOT NULL
);

CREATE INDEX IF NOT EXISTS ops_failure_run_stage ON ops_failure (env, bronze_run_id, stage);
CREATE INDEX IF NOT EXISTS ops_failure_subject_key ON ops_failure (subject_key);

CREATE TABLE IF NOT EXISTS ops_schema_snapshot (
    id             bigserial    PRIMARY KEY,
    env            text         NOT NULL CHECK (env IN ('test', 'prod')),
    layer          text         NOT NULL CHECK (layer IN ('silver', 'gold')),
    table_name     text         NOT NULL,
    physical_name  text         NOT NULL,
    schema_id      integer      NOT NULL,
    fingerprint    char(64)     NOT NULL,
    columns        jsonb        NOT NULL,
    observed_at    timestamptz  NOT NULL,
    bronze_run_id  text
);

CREATE INDEX IF NOT EXISTS ops_schema_snapshot_table ON ops_schema_snapshot (env, table_name, observed_at);

COMMENT ON TABLE ops_run_summary IS '실행 1번의 단계 1개 = 1행. 운영 화면 탭1 핵심 숫자·단계 흐름도. "오늘" = 마지막으로 성공한 Bronze 실행';
COMMENT ON TABLE ops_failure IS '실패한 대상 1개 = 1행. 운영 화면 실패 드릴다운(원인별 → 건별 → 증거)과 공고 추적의 출처';
COMMENT ON TABLE ops_schema_snapshot IS 'Silver·Gold Iceberg 표의 실제 칼럼 관측. 운영 화면 탭2 스키마 지문 비교의 실제값';
