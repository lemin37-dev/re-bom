-- 001_init.sql — pgvector 확장과 사진 임베딩 테이블 (rebom DB, rebom 사용자로 실행)
-- 다시 실행해도 안전하다(IF NOT EXISTS). 스키마 설명: docs/data_contract.md "사진 임베딩 (pgvector)".
-- RDS 는 vector 확장을 rds_superuser 만 만들 수 있다. 처음 한 번은 관리자 계정(rds!db-... 시크릿)으로
-- `CREATE EXTENSION IF NOT EXISTS vector;` 를 먼저 실행한 뒤 이 파일을 rebom 사용자로 실행한다.

CREATE EXTENSION IF NOT EXISTS vector;

-- 공고 사진 칸(popfile1~8)마다 모델별 벡터 1개. 같은 칸의 사진이 바뀌면(sha256 변경) 그 행을 덮어쓴다.
CREATE TABLE IF NOT EXISTS image_embedding (
    desertion_no  text        NOT NULL,                 -- 공고 번호 (API desertionNo)
    image_slot    smallint    NOT NULL CHECK (image_slot BETWEEN 1 AND 8),
    model         text        NOT NULL,                 -- 예: gemini-embedding-2
    photo_version smallint    NOT NULL DEFAULT 1,       -- manifest version (v002 → 2)
    sha256        char(64)    NOT NULL,                 -- 임베딩한 원본 파일 해시 (manifest 와 대조)
    s3_key        text        NOT NULL,                 -- bronze/rescue/images/<desertionNo>/popfile<N>[_vNNN].<ext>
    photo_run_id  text        NOT NULL,                 -- 이 사진이 들어온 사진 실행 run_id
    embedding     vector(768) NOT NULL,                 -- output_dimensionality=768 (HNSW 한도 2,000 이하)
    embedded_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (desertion_no, image_slot, model)
);

-- 코사인 거리 검색. gemini-embedding-2 의 잘린 차원 출력은 API 가 정규화한다.
CREATE INDEX IF NOT EXISTS image_embedding_hnsw_cos
    ON image_embedding USING hnsw (embedding vector_cosine_ops);
