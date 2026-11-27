-- 009: 운영 기록 실패 원인에 수집 API 오류 종류를 더한다 (운영 화면 "실패 원인별 목록").
--   api_result_error   HTTP 200 인데 resultCode 가 00 이 아님 (재시도해서 받았으면 evidence.recovered = true)
--   api_timeout        응답 시간 초과
--   api_http_error     HTTP 200 아님·연결 오류
--   invalid_partition  manifest 구간의 날짜·건수 형식이 틀림 (invalid_date, invalid_date_range, invalid_count)
-- 008 의 값 목록 CHECK(이름은 PostgreSQL 기본 이름 ops_failure_reason_code_check)를 같은 이름으로 바꾼다.
-- 008 은 고치지 않는다(이미 있는 마이그레이션은 CI 가 막음). 순서는 rebom.common.ops_log.REASON_CODES 와 같다.

ALTER TABLE ops_failure DROP CONSTRAINT IF EXISTS ops_failure_reason_code_check;
ALTER TABLE ops_failure ADD CONSTRAINT ops_failure_reason_code_check CHECK (reason_code IN (
    'aborted', 'count_mismatch', 'dropped_row', 'not_found', 'replaced_not_found', 'gave_up',
    'download_failed', 'raw_content_conflict', 'silver_transform_conflict',
    'status_history_conflict', 'partition_incomplete', 'publish_blocked', 'stale_input',
    'config_hash_mismatch', 'no_active_version', 'fk_skipped', 'sync_error',
    'api_result_error', 'api_timeout', 'api_http_error', 'invalid_partition'));
