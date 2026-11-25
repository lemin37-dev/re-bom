-- 006_embedding_drop_config_columns.sql — 벡터 표에서 version 으로 정해지는 칼럼을 뺀다 (rebom DB, rebom 사용자로 실행)
-- 2026-11-25 lemin 결정: 모델·카드 템플릿·태그 버전은 Gold 레지스트리의 version 설정(task@N)에 들어 있으므로
--   행마다 다시 적지 않는다. 행에는 version 과 입력 해시(text_sha256, 사진 sha256)만 둔다.
--   검색 때 질의 모델·차원은 active_version.query_config 에서 읽는다.
-- 빼는 칼럼: image_embedding.model, text_embedding.model_key · template_version · tag_versions
-- 표는 004 에서 새로 만들어 비어 있다. 적용: python -m rebom.common.migrate apply (사람이)

ALTER TABLE image_embedding DROP COLUMN IF EXISTS model;

ALTER TABLE text_embedding
    DROP COLUMN IF EXISTS model_key,
    DROP COLUMN IF EXISTS template_version,
    DROP COLUMN IF EXISTS tag_versions;
