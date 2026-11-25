-- 004_vector_tables_v01.sql — 벡터 표 2개를 스키마 v0.1 모양으로 다시 만든다 (rebom DB, rebom 사용자로 실행)
-- 2026-11-25 lemin 결정: RDS 에 있는 데이터는 지우고 S3(Silver·Gold) → RDS 동기화를 처음부터 한다.
--   사진 HNSW(image_embedding_hnsw_cos)도 지운다. v0.1 은 인덱스 없이 전체 비교로 검색하고,
--   실측한 뒤 v0.2 에서 HNSW 도입을 고려한다(도입하면 다음 번호 파일에 넣는다).
-- 바뀌는 점: 키에 version 추가(같은 모델 v1·v2 공존), 서비스 표로 외래키, gold_run_id 추가,
--   text_embedding 은 tag_versions 추가·template_version A1/A2 CHECK 제거.
-- 003_service_tables.sql 을 먼저 적용해야 한다(외래키 대상). 이 표는 새 S3 동기화 기능이 채운다.
-- 한 트랜잭션으로 돈다(python -m rebom.common.migrate 가 파일 단위로 묶음. 손으로 돌릴 때는 BEGIN/COMMIT 으로 감쌀 것).
-- ⚠ 기존 행을 모두 지운다. 적용은 사람이 승인한 뒤 직접 실행한다.
-- ⚠ 적용 뒤에는 기존 DAG task load_photo_embeddings(embedding/store.py, ON CONFLICT (desertion_no, image_slot, model))가
--   실패한다. 코드는 고치지 않으므로, 적용 전에 Airflow 에서 해당 DAG 를 멈추거나 그 task 실패를 감안한다.


DROP INDEX IF EXISTS image_embedding_hnsw_cos;
DROP TABLE IF EXISTS image_embedding;
DROP TABLE IF EXISTS text_embedding;

-- ───────── 검색 벡터 영역 ─────────
CREATE TABLE image_embedding (
    desertion_no  varchar(20)  NOT NULL,
    image_slot    smallint     NOT NULL CHECK (image_slot BETWEEN 1 AND 8),
    version       varchar(64)  NOT NULL CHECK (version LIKE 'image_embedding@%'),
    model         text         NOT NULL,
    photo_version smallint     NOT NULL DEFAULT 1,
    sha256        char(64)     NOT NULL,
    s3_key        text         NOT NULL,
    photo_run_id  text         NOT NULL,
    gold_run_id   text,
    embedding     vector(768)  NOT NULL,
    embedded_at   timestamptz  NOT NULL DEFAULT now(),
    PRIMARY KEY (desertion_no, image_slot, version),
    FOREIGN KEY (desertion_no, image_slot) REFERENCES service_notice_photo (desertion_no, image_slot) ON DELETE CASCADE
);
-- v0.1 은 벡터 인덱스 없음(전체 비교). 검색은 active_version 의 version 과 service_notice_photo.sha256 일치로 거른다.

CREATE TABLE text_embedding (
    desertion_no     varchar(20)  NOT NULL REFERENCES service_notice (desertion_no) ON DELETE CASCADE,
    text_kind        varchar(10)  NOT NULL DEFAULT 'card' CHECK (text_kind IN ('card')),
    version          varchar(64)  NOT NULL CHECK (version LIKE 'text_embedding@%'),
    model_key        text         NOT NULL,
    text_sha256      char(64)     NOT NULL,
    template_version varchar(10)  NOT NULL,
    tag_versions     jsonb,
    gold_run_id      text,
    embedding        vector(768)  NOT NULL,
    embedded_at      timestamptz  NOT NULL DEFAULT now(),
    PRIMARY KEY (desertion_no, text_kind, version)
);



COMMENT ON TABLE image_embedding IS '공고 현재 사진 칸의 검색 벡터(칸 × 버전). Gold gold_image_embedding(sha256 단위)을 칸에 펼침. 검색 후보 = 보호중 + sha256 일치 + 활성 버전. 키에 version 포함(004). v0.1 인덱스 없음(전체 비교), HNSW 는 실측 뒤 v0.2 에서 검토';
COMMENT ON COLUMN image_embedding.desertion_no IS '공고번호
예: 448567202600123';
COMMENT ON COLUMN image_embedding.image_slot IS '사진 칸 1~8
예: 1';
COMMENT ON COLUMN image_embedding.version IS 'image_embedding@N. 키에 포함해 같은 모델 v1·v2 공존
예: image_embedding@1';
COMMENT ON COLUMN image_embedding.model IS '모델 이름(:차원)
예: gemini-embedding-2:768';
COMMENT ON COLUMN image_embedding.photo_version IS '사진 교체 순번
예: 2';
COMMENT ON COLUMN image_embedding.sha256 IS '임베딩한 원본 sha256. service_notice_photo.sha256 과 다르면 검색 후보에서 제외
예: 3b7a…c90d';
COMMENT ON COLUMN image_embedding.s3_key IS '임베딩한 원본 키
예: bronze/rescue/images/448567202600123/popfile1_v002.jpg';
COMMENT ON COLUMN image_embedding.photo_run_id IS '사진 실행 run_id
예: 20261124T030000-ab12';
COMMENT ON COLUMN image_embedding.gold_run_id IS 'Gold 실행 ID';
COMMENT ON COLUMN image_embedding.embedding IS '768차원 벡터, 코사인 거리. v0.1 인덱스 없음
예: [0.012, -0.034, …] (768개)';
COMMENT ON COLUMN image_embedding.embedded_at IS 'RDS 적재 시각';
COMMENT ON TABLE text_embedding IS '공고 카드의 글 검색 벡터(공고 × 버전). Gold gold_text_embedding 중 현재 카드 해시와 맞는 1행. 키에 version 포함·A1/A2 CHECK 제거(004). v0.1 인덱스 없음';
COMMENT ON COLUMN text_embedding.desertion_no IS '공고번호
예: 448567202600123';
COMMENT ON COLUMN text_embedding.text_kind IS '글 종류 (지금은 card 하나)
예: card';
COMMENT ON COLUMN text_embedding.version IS 'text_embedding@N (템플릿 내용·지문·태그 버전 포함 설정)
예: text_embedding@3';
COMMENT ON COLUMN text_embedding.model_key IS '모델 이름(:차원)
예: gemini-embedding-2:768';
COMMENT ON COLUMN text_embedding.text_sha256 IS '가린 카드 본문 sha256 (Gold source_hash)
예: 4547c5…6206';
COMMENT ON COLUMN text_embedding.template_version IS '카드 템플릿 (Gold prompt_version)
예: A2';
COMMENT ON COLUMN text_embedding.tag_versions IS '카드에 쓴 작업별 태그 버전
예: {"photo_trait": "photo_trait@1", "text_personality": "text_personality@1"}';
COMMENT ON COLUMN text_embedding.gold_run_id IS 'Gold 실행 ID
예: text-737c35ec…';
COMMENT ON COLUMN text_embedding.embedding IS '768차원 벡터. v0.1 은 인덱스 없이 전체 비교';
COMMENT ON COLUMN text_embedding.embedded_at IS 'RDS 적재 시각';
