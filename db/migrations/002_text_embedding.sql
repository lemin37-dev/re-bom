-- 002_text_embedding.sql — 공고 글 카드 임베딩 테이블 (rebom DB, rebom 사용자로 실행)
-- 다시 실행해도 안전하다(IF NOT EXISTS). 001_init.sql 의 vector 확장이 먼저 있어야 한다.
-- 계획: docs/image_embedding_pipeline.md 10절. 모델은 사진과 같은 gemini-embedding-2(768) (11-18 결정).
-- ⚠ 적용은 사람 승인 뒤. 첫 적재는 Silver 태그 칼럼이 완성된 뒤 A2 템플릿으로 한다.

-- 공고 1건 × 글 종류(text_kind) × 모델마다 벡터 1개. 카드 내용이 바뀌면(text_sha256 변경) 그 행을 덮어쓴다.
CREATE TABLE IF NOT EXISTS text_embedding (
    desertion_no     text        NOT NULL,              -- 공고 번호 (API desertionNo)
    text_kind        text        NOT NULL DEFAULT 'card' CHECK (text_kind IN ('card')),
    model_key        text        NOT NULL,              -- 예: gemini-embedding-2:768
    text_sha256      char(64)    NOT NULL,              -- 카드(공백 정리 후, 접두어 없이) 해시
    template_version text        NOT NULL CHECK (template_version IN ('A1', 'A2')),
    embedding        vector(768) NOT NULL,
    embedded_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (desertion_no, text_kind, model_key)
);

-- v0.1 은 글 벡터 인덱스 없이 전체 비교로 검색한다 (2026-11-24 결정. 사진 image_embedding 의 HNSW 는 백필이 끝나 그대로 둔다).
-- HNSW 는 v0.1 실측 확인 후 v0.2 에서 도입을 고려한다. 도입하면 다음 번호 마이그레이션에 넣는다.
