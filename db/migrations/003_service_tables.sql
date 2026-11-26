-- 003_service_tables.sql — 서비스 화면용 표 5개 (rebom DB, rebom 사용자로 실행)
-- 설계 기준: 노션 "Gold 스키마 · 확정 규칙과 사진 임베딩 설계안"(11/22~) RDS 절. 그림·설명 원본: docs/rds_schema.dbml
-- 표: service_notice · service_notice_photo · service_notice_tag · active_version · sync_state
-- 다시 실행해도 안전하다(IF NOT EXISTS). 기존 표(001 image_embedding, 002 text_embedding)는 건드리지 않는다.
--   벡터 표의 version 키 추가와 service_notice_photo 외래키는 적재 코드(embedding/store.py) 변경과 함께 다음 번호 파일에서 한다.
-- 값 목록 칼럼은 PostgreSQL ENUM 타입이 아니라 text/varchar + CHECK 로 둔다(값 추가·삭제가 쉬움).
--   tag_code 는 품종이 열린 값(breed.<품종>)이라 CHECK 를 두지 않는다. 귀염은 검색 태그가 될 수 없다(CHECK).
-- ⚠ 적용은 사람이 승인한 뒤 직접 실행한다.

-- ───────── 공고 화면 영역 ─────────
CREATE TABLE IF NOT EXISTS service_notice (
    desertion_no          varchar(20)  PRIMARY KEY,
    notice_no             text,
    status_group          varchar(12)  NOT NULL CHECK (status_group IN ('PROTECTING', 'CLOSED', 'UNKNOWN')),
    process_state_raw     text,
    closure_reason_code   text,
    closed_transition_at  timestamptz,
    visible_until         timestamptz,
    species_code          varchar(10)  NOT NULL,
    species_name          text,
    breed_code            varchar(10),
    breed_name_raw        text,
    breed_full_name_raw   text,
    is_mix                boolean,
    sex_code              char(1)      CHECK (sex_code IN ('M', 'F', 'Q')),
    neuter_code           char(1)      CHECK (neuter_code IN ('Y', 'N', 'U')),
    birth_year            smallint,
    weight_raw            text,
    weight_kg             numeric(6, 2),
    size                  varchar(10)  CHECK (size IN ('small', 'medium', 'large')),
    color_raw             text,
    color_tag             text[],
    color_pattern         text,
    special_mark          text,
    sfe_soci              text,
    sfe_health            text,
    health_chk            text,
    vaccination_chk       text,
    happen_date           date,
    happen_place          text,
    org_name              text,
    notice_start_date     date,
    notice_end_date       date,
    care_reg_no           varchar(20),
    care_name             text,
    care_tel              text,
    care_address          text,
    content_hash          char(64)     NOT NULL,
    silver_publish_seq    bigint,
    synced_at             timestamptz  NOT NULL DEFAULT now(),
    CHECK (visible_until IS NULL OR closed_transition_at IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS service_notice_search ON service_notice (species_code) WHERE status_group = 'PROTECTING';
CREATE INDEX IF NOT EXISTS service_notice_visible ON service_notice (visible_until) WHERE visible_until IS NOT NULL;

COMMENT ON TABLE service_notice IS '공고 화면의 기준 행(공고당 1행). 검색은 PROTECTING 만, 상세는 PROTECTING 이거나 visible_until 이 지나지 않은 종료 공고. 공개 필드만, 보호자 실명(careOwnerNm)·동물등록번호 없음. 원천: Silver silver_animal_notice + silver_shelter';
COMMENT ON COLUMN service_notice.desertion_no IS '공고 고유키 (API desertionNo)
예: 448567202600123';
COMMENT ON COLUMN service_notice.notice_no IS '공고번호 문자열 (noticeNo)
예: 충남-당진-2026-00542';
COMMENT ON COLUMN service_notice.status_group IS '공고 상태. Silver 값 그대로, RDS 가 판단하지 않음. 공고 상태 동기화 경로만 고침
예: PROTECTING';
COMMENT ON COLUMN service_notice.process_state_raw IS '상태 원문. 상세에 그대로 표시
예: 보호중 / 종료(입양)';
COMMENT ON COLUMN service_notice.closure_reason_code IS '종료 사유. 바뀌어도 30일 연장 안 함
예: 입양';
COMMENT ON COLUMN service_notice.closed_transition_at IS '서비스 시작 이후 보호중→종료를 처음 확인한 시각. 같은 종료 재처리 시 유지, 보호중 복귀 시 비움 (출처는 광훈 님 확인 대기)
예: 2026-11-24 03:12:00+09';
COMMENT ON COLUMN service_notice.visible_until IS '상세 노출 만료 = closed_transition_at + 30일. 재동기화로 늘리지 않음. 서비스 시작 전 종료 공고는 NULL(비노출)
예: 2026-12-24 03:12:00+09';
COMMENT ON COLUMN service_notice.species_code IS '축종 코드. 벡터 검색의 종 필터는 이 칼럼과 JOIN (벡터 표에 복사 안 함)
예: 417000';
COMMENT ON COLUMN service_notice.species_name IS '축종 이름 (upKindNm)
예: 개';
COMMENT ON COLUMN service_notice.breed_code IS '품종 코드 (kindCd, 앞자리 0 보존)
예: 000114';
COMMENT ON COLUMN service_notice.breed_name_raw IS '품종 원문 (kindNm). 화면 품종은 이 값 (태깅 결과 아님)
예: 믹스견';
COMMENT ON COLUMN service_notice.breed_full_name_raw IS 'kindFullNm. 기타 축종은 여기에만 실제 이름
예: [개] 믹스견';
COMMENT ON COLUMN service_notice.is_mix IS '믹스 여부 (코드 000114 등 또는 이름에 ''믹스'')
예: true';
COMMENT ON COLUMN service_notice.sex_code IS '성별
예: F';
COMMENT ON COLUMN service_notice.neuter_code IS '중성화
예: U';
COMMENT ON COLUMN service_notice.birth_year IS '출생연도. 화면은 ''N년생''만, 나이는 안 보여 줌
예: 2024';
COMMENT ON COLUMN service_notice.weight_raw IS '체중 원문 (화면 표시)
예: 9.00(Kg)';
COMMENT ON COLUMN service_notice.weight_kg IS '체중 kg (범위면 큰 값)
예: 9.00';
COMMENT ON COLUMN service_notice.size IS '크기 (개만). 크기 필터
예: small';
COMMENT ON COLUMN service_notice.color_raw IS '털색 원문 (colorCd)
예: 갈색&검정';
COMMENT ON COLUMN service_notice.color_tag IS '털색 계열 배열 (값은 color_group). 털색 필터
예: {갈색계열,검정계열}';
COMMENT ON COLUMN service_notice.color_pattern IS '무늬. 없으면 NULL
예: 얼룩';
COMMENT ON COLUMN service_notice.special_mark IS '특이사항 원문 (specialMark)
예: 온순. 겁 조금.';
COMMENT ON COLUMN service_notice.sfe_soci IS '특징(사회성) 원문. 상세 성격 자리
예: 사람을 잘 따름';
COMMENT ON COLUMN service_notice.sfe_health IS '특징(건강) 원문
예: 피부병 치료 중';
COMMENT ON COLUMN service_notice.health_chk IS '건강 체크 원문 (healthChk)';
COMMENT ON COLUMN service_notice.vaccination_chk IS '접종 체크 원문 (vaccinationChk)';
COMMENT ON COLUMN service_notice.happen_date IS '접수일 (happenDt)
예: 2026-11-10';
COMMENT ON COLUMN service_notice.happen_place IS '발견 장소 원문. 화면은 읍·면·동/구까지만
예: 당진시 용연동';
COMMENT ON COLUMN service_notice.org_name IS '관할 기관 (orgNm)
예: 충청남도 당진시';
COMMENT ON COLUMN service_notice.notice_start_date IS '공고 시작일 (noticeSdt)
예: 2026-11-11';
COMMENT ON COLUMN service_notice.notice_end_date IS '공고 종료일 (noticeEdt)
예: 2026-11-23';
COMMENT ON COLUMN service_notice.care_reg_no IS '보호소 등록번호 (보호소 표는 따로 두지 않고 공고에 펼침)
예: 344500202100001';
COMMENT ON COLUMN service_notice.care_name IS '보호소 이름
예: 당진시동물보호소';
COMMENT ON COLUMN service_notice.care_tel IS '보호소 전화. 화면 표시(11/20 결정), 검색 로그로 보내지 않음
예: 000-0000-0000';
COMMENT ON COLUMN service_notice.care_address IS '보호소 주소
예: 충청남도 당진시 ○○로 00';
COMMENT ON COLUMN service_notice.content_hash IS '옮긴 칼럼 값의 sha256. 같으면 UPDATE 건너뜀(재반영 무해)
예: 9f2c…e1a0 (64자)';
COMMENT ON COLUMN service_notice.silver_publish_seq IS '이 행을 쓴 Silver 완료본 순번 (silver_run_id 방식이 정해지면 교체)
예: 42';
COMMENT ON COLUMN service_notice.synced_at IS 'RDS 반영 시각
예: 2026-11-25 04:00:00+09';

CREATE TABLE IF NOT EXISTS service_notice_photo (
    desertion_no    varchar(20)  NOT NULL REFERENCES service_notice (desertion_no) ON DELETE CASCADE,
    image_slot      smallint     NOT NULL CHECK (image_slot BETWEEN 1 AND 8),
    sha256          char(64)     NOT NULL,
    photo_version   smallint     NOT NULL,
    serving_s3_key  text         NOT NULL,
    thumb_s3_key    text,
    silver_publish_seq bigint,
    synced_at       timestamptz  NOT NULL DEFAULT now(),
    PRIMARY KEY (desertion_no, image_slot)
);
CREATE INDEX IF NOT EXISTS service_notice_photo_sha256 ON service_notice_photo (sha256);

COMMENT ON TABLE service_notice_photo IS '공고 화면에 보여줄 사진 칸(칸당 1행). 원천: Silver 현재 사진 표시 키. emptied 확정 칸은 행 삭제, 종료 공고는 마지막 확인 사진 유지';
COMMENT ON COLUMN service_notice_photo.desertion_no IS '공고번호
예: 448567202600123';
COMMENT ON COLUMN service_notice_photo.image_slot IS 'popfile 칸 1~8. 대표 사진 = 키가 있는 가장 작은 칸
예: 1';
COMMENT ON COLUMN service_notice_photo.sha256 IS '표시 중인 원본 사진 sha256. 태그·벡터가 지금 사진 것인지 확인하는 키
예: 3b7a…c90d (64자)';
COMMENT ON COLUMN service_notice_photo.photo_version IS '사진 교체 순번 (Silver current_version)
예: 2';
COMMENT ON COLUMN service_notice_photo.serving_s3_key IS '화면 이미지 키 (CloudFront /images/*)
예: serving/rescue/images/448567202600123/popfile1_v002.webp';
COMMENT ON COLUMN service_notice_photo.thumb_s3_key IS '썸네일 키 (~400px WebP)
예: thumb/rescue/images/448567202600123/popfile1_v002.webp';
COMMENT ON COLUMN service_notice_photo.silver_publish_seq IS '이 행을 쓴 Silver 공통 완료본 순번
예: 42';
COMMENT ON COLUMN service_notice_photo.synced_at IS 'RDS 반영 시각';

CREATE TABLE IF NOT EXISTS service_notice_tag (
    desertion_no    varchar(20)  NOT NULL REFERENCES service_notice (desertion_no) ON DELETE CASCADE,
    subject_type    varchar(10)  NOT NULL CHECK (subject_type IN ('photo', 'animal')),
    subject_key     varchar(64)  NOT NULL,
    task            varchar(40)  NOT NULL,
    tag_code        varchar(100) NOT NULL,
    tag_label       text         NOT NULL,
    version         varchar(64)  NOT NULL,
    source          varchar(10)  NOT NULL CHECK (source IN ('photo', 'text', 'rule')),
    searchable      boolean      NOT NULL,
    displayable     boolean      NOT NULL,
    rank            smallint,
    similarity      smallint,
    gold_run_id     text,
    synced_at       timestamptz  NOT NULL DEFAULT now(),
    PRIMARY KEY (desertion_no, subject_type, subject_key, task, tag_code),
    CHECK (tag_code NOT IN ('cute', 'impression.cute') OR NOT searchable)
);
CREATE INDEX IF NOT EXISTS service_notice_tag_search ON service_notice_tag (tag_code, desertion_no) WHERE searchable;

COMMENT ON TABLE service_notice_tag IS '공고에 연결한 태그 1개 = 1행. Gold gold_animal_tag 중 활성 버전 + 현재 입력(대표 사진 sha256 / 최신 글 해시)만. 검색은 보호중 공고의 searchable, 상세는 displayable';
COMMENT ON COLUMN service_notice_tag.desertion_no IS '공고번호
예: 448567202600123';
COMMENT ON COLUMN service_notice_tag.subject_type IS '결과 대상 종류
예: photo';
COMMENT ON COLUMN service_notice_tag.subject_key IS 'photo 면 대표 사진 sha256 (service_notice_photo.sha256 과 같아야 함), animal 이면 공고번호
예: 3b7a…c90d';
COMMENT ON COLUMN service_notice_tag.task IS '태그를 만든 작업
예: photo_trait';
COMMENT ON COLUMN service_notice_tag.tag_code IS '태그 코드 (목록은 Enum tag_code)
예: ear.drop';
COMMENT ON COLUMN service_notice_tag.tag_label IS '화면 이름 (적재 때 tag_codes.py 라벨 복사)
예: 늘어진 귀';
COMMENT ON COLUMN service_notice_tag.version IS '이 태그를 만든 버전 task@N. active_version 과 같아야 검색·표시
예: photo_trait@1';
COMMENT ON COLUMN service_notice_tag.source IS '출처
예: photo';
COMMENT ON COLUMN service_notice_tag.searchable IS '검색에 쓰는지 (Gold 행 값). 품종은 유사도 90 이상만 true
예: true';
COMMENT ON COLUMN service_notice_tag.displayable IS '화면에 보이는지 (Gold 행 값). breed.* 는 false
예: true';
COMMENT ON COLUMN service_notice_tag.rank IS '품종 순위 1~3 (photo_breed 만)
예: 1';
COMMENT ON COLUMN service_notice_tag.similarity IS '품종 유사도 평균 또는 Jev 확률×100. 화면 비표시
예: 92';
COMMENT ON COLUMN service_notice_tag.gold_run_id IS 'Gold 실행 ID (추적용)
예: photo-tags-20261123T0300';
COMMENT ON COLUMN service_notice_tag.synced_at IS 'RDS 반영 시각';

-- ───────── 운영 영역 ─────────
CREATE TABLE IF NOT EXISTS active_version (
    task             varchar(40)  PRIMARY KEY,
    version          varchar(64)  NOT NULL,
    config_hash      char(64)     NOT NULL,
    query_config     jsonb,
    previous_version varchar(64),
    activated_at     timestamptz  NOT NULL DEFAULT now(),
    activated_by     text         NOT NULL,
    CHECK (version LIKE task || '@%')
);

COMMENT ON TABLE active_version IS '작업별 서비스 활성 버전(작업당 1행). 일일 배치는 시작 때 한 번 읽어 고정, 검색 API 는 한 요청 안에서 활성값·질의 설정·벡터를 함께 씀. 전환·롤백은 사람이 (11/23 기준안, 칼럼은 제안)';
COMMENT ON COLUMN active_version.task IS '작업 이름
예: image_embedding';
COMMENT ON COLUMN active_version.version IS '활성 버전 (Gold gold_version_registry 등록값)
예: image_embedding@1';
COMMENT ON COLUMN active_version.config_hash IS 'Gold 레지스트리 config_hash. 전환 때 대조';
COMMENT ON COLUMN active_version.query_config IS '검색에 필요한 설정(질의 모델·차원 등). 원본은 Gold 레지스트리
예: {"model": "gemini-embedding-2", "dim": 768}';
COMMENT ON COLUMN active_version.previous_version IS '직전 활성 버전 (롤백용)
예: image_embedding@0';
COMMENT ON COLUMN active_version.activated_at IS '전환 시각';
COMMENT ON COLUMN active_version.activated_by IS '전환한 사람
예: lemin';

CREATE TABLE IF NOT EXISTS sync_state (
    route               varchar(20)  PRIMARY KEY CHECK (route IN ('notice_status', 'photo_vector', 'tag', 'text_vector')),
    last_publish_seq    bigint,
    last_silver_run_id  text,
    notice_snapshot_id  bigint,
    image_snapshot_id   bigint,
    applied_at          timestamptz  NOT NULL DEFAULT now()
);

COMMENT ON TABLE sync_state IS '동기화 경로별 마지막 반영 Silver 완료본 (워터마크 대신, 11/23 제안). 더 오래된 완료본은 반영 안 함. 순번 비교와 행 변경은 같은 트랜잭션 (표 자체가 제안)';
COMMENT ON COLUMN sync_state.route IS '동기화 경로
예: photo_vector';
COMMENT ON COLUMN sync_state.last_publish_seq IS 'silver_publish_log 방식일 때 마지막 반영 순번
예: 42';
COMMENT ON COLUMN sync_state.last_silver_run_id IS 'journal COMPLETED 방식일 때 마지막 반영 실행. 둘 중 하나만 씀(미정)
예: silver_20261124T030012-f65718';
COMMENT ON COLUMN sync_state.notice_snapshot_id IS '읽은 Silver 공고 Iceberg 스냅샷
예: 5620324469304075651';
COMMENT ON COLUMN sync_state.image_snapshot_id IS '읽은 Silver 현재 사진 Iceberg 스냅샷
예: 800755379067985557';
COMMENT ON COLUMN sync_state.applied_at IS '반영 완료 시각';
