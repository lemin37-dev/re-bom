-- 007_image_embedding_drop_photo_run_id.sql — 사진 벡터 표에서 photo_run_id 를 뺀다 (rebom DB, rebom 사용자로 실행)
-- 2026-11-25 lemin 결정: 이 칸을 읽는 코드가 없고(예전 embedding/store.py load 가 쓰기만 함), 다른 벡터·태그 표에는 원천 실행 칸이 없다.
--   사진은 sha256·s3_key 로, 반영 시점은 service_notice_photo.silver_publish_seq(→ Silver 발행 기록의 photo_run_id)로,
--   모델 실행은 gold_run_id 로 추적한다. Bronze·Silver 의 photo_run_id 는 그대로 둔다.
-- 표는 004 에서 새로 만들어 아직 비어 있다. 적용: python -m rebom.common.migrate apply (사람이)

ALTER TABLE image_embedding DROP COLUMN IF EXISTS photo_run_id;
