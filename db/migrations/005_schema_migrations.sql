-- 005_schema_migrations.sql — 마이그레이션 적용 기록 표 (rebom DB, rebom 사용자로 실행)
-- 어떤 db/migrations 파일을 언제·누가·어떤 내용(sha256)으로 적용했는지 1파일 = 1행으로 남긴다.
-- 적용 도구: python -m rebom.common.migrate (status / apply / baseline). 도구가 이 표를 같은 모양으로 먼저 만들어 두므로
--   이 파일은 표 정의를 레포에 남기는 용도이고, 다시 실행해도 안전하다(IF NOT EXISTS).
-- 이 표가 생기기 전에 손으로 적용한 001~004 는 `baseline 004` 로 적용 기록만 넣는다.

CREATE TABLE IF NOT EXISTS schema_migrations (
    filename    text         PRIMARY KEY,
    sha256      char(64)     NOT NULL,
    applied_at  timestamptz  NOT NULL DEFAULT now(),
    applied_by  text         NOT NULL,
    how         varchar(10)  NOT NULL CHECK (how IN ('apply', 'baseline'))
);

COMMENT ON TABLE schema_migrations IS '마이그레이션 적용 기록(파일당 1행). python -m rebom.common.migrate 가 쓴다. 적용한 파일 내용이 레포와 다르면 도구가 멈춤';
COMMENT ON COLUMN schema_migrations.filename IS 'db/migrations 파일 이름
예: 004_vector_tables_v01.sql';
COMMENT ON COLUMN schema_migrations.sha256 IS '적용할 때 파일 내용(UTF-8, 줄바꿈 LF 기준)의 sha256. 레포 파일과 다르면 적용 뒤 파일이 고쳐진 것
예: 9f2c…e1a0 (64자)';
COMMENT ON COLUMN schema_migrations.applied_at IS '적용(또는 기록) 시각
예: 2026-11-25 14:10:00+09';
COMMENT ON COLUMN schema_migrations.applied_by IS '적용한 사람 (--by, 없으면 PC 사용자 이름)
예: lemin';
COMMENT ON COLUMN schema_migrations.how IS 'apply = 도구가 실행, baseline = 이미 손으로 적용한 파일의 기록만 넣음
예: apply';
