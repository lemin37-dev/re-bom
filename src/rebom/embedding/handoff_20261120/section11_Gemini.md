## 10. 글(텍스트) 벡터 관리 계획 [예정]

아직 코드는 없습니다. 사진 파이프라인과 같은 원칙(S3가 원본, RDS는 서비스용 사본, 해시로 변경분만)과 **같은 모델**(`gemini-embedding-2`, 768차원)을 씁니다. 결정 근거는 `docs/handoff/임베딩_결정사항_인계_20261118.md`에 있습니다 (11-18: 모델 하나로 단순화, EG2는 쓰지 않음).

사진과 같은 벡터 공간이라, 글 질의로 사진 벡터를 찾는 것도 추가 모델 없이 가능합니다. 이 경로를 쓸지는 효과를 재고 정합니다(10.5).

### 10.1 무엇을 임베딩하나

| 항목 | 내용 |
|---|---|
| 입력 | Silver 공고 테이블(Glue `rebom_silver`, 키 `desertion_no`)의 현재 공고 + 태그 칼럼(비전 태깅·Jev) |
| 단위 | 공고 1건 = 카드 1개 (`text_kind = card`) |
| 모델 | `gemini-embedding-2`, `output_dimensionality=768`, 받은 벡터는 L2 정규화 |
| 입력 형식 | 저장하는 카드는 `"title: none \| text: " + 카드`, 검색어는 `"task: search result \| query: " + 검색어` (벤치마크 I2와 같음). `gemini.py`의 `document_text`·`query_text`가 이 형식과 같은지 확인 |
| 요청 | 요청 1번에 여러 문장 (벤치마크는 20개, `TEXT_BATCH` 100도 결과는 같음). 문장마다 Content 1개 |
| 카드 내용 | **A 템플릿**. `text_card_reference.py`의 `build_card()`와 글자까지 같게 만듭니다 (벤치마크 글 960/960 일치) |
| Silver 칼럼 | Silver 이름을 API 이름(kindNm·age·sexCd·neuterYn·weight·colorCd·specialMark·sfeSoci)으로 바꿔 넣습니다. Silver의 `color_tag`·`size`는 쓰지 않고, 원래 털색·몸무게 값에서 같은 규칙으로 다시 계산합니다. 구현 전에 `silver_schema_check.py`로 이름·코드 값(sexCd가 M/F인지 등)을 확인합니다. 정의표와 규칙이 다른 부분은 `silver_정의표_템플릿_비교_20261118.md` |
| 제외 | 보호자 실명(`careOwnerNm`)·연락처(AGENTS.md 1번). 특징 글 안의 전화번호는 **API로 보내기 전에** `[번호]`로 가리고, 앞의 관리 번호는 지웁니다 |
| 발견 장소 | 카드에 넣지 않고 지역 필터로 씁니다 [제안] |

**A 템플릿 칼럼** (벤치마크 순서 그대로, 값이 없는 항목은 뺌)

| 순서 | 원본 칼럼 | 카드에 쓰는 형태 |
|---|---|---|
| 1 | kindNm | 그대로 (예: 믹스견) |
| 2 | age | `2026년생` (네 자리 연도만) |
| 3 | sexCd | 수컷 / 암컷 (미상은 뺌) |
| 4 | neuterYn | 중성화함 / 중성화 안 함 (미상은 뺌) |
| 5 | weight | `2.3kg 소형` — A1(벤치마크): 10kg 이하 소형 · A2(11-20, Silver 규칙): 10kg 미만 소형 · 10~25kg 미만 중형 · 25kg 이상 대형. `size_of(k, template)` |
| 6 | colorCd | `털색 흰색계열·검정계열` (5계열 사전) + `(공고 털색 <원래 값>)` |
| 7 | (A2) 태그 | `털 길이 장모`처럼 `이름표 값`, 확인불가·빈 값은 뺌 |
| 8 | specialMark (sfeSoci는 A2에서 결정) | `. 특징: <글>` |

**템플릿 버전**
- `A1` = 1~6 + 8 (벤치마크와 같음, 사회성 sfeSoci 없음). **smoke ① 카드 일치 확인에만** 씁니다.
- `A2` = A1 + 7(태그). **첫 적재는 A2로 한 번에** 합니다. 태그는 각 담당자가 로컬에서 성능을 확인하고 Silver 칼럼으로 붙인 뒤 씁니다.
  - 태그 칼럼 이름·값 목록·이름표 말은 담당자와 정합니다 [결정 필요].
  - 사회성(sfeSoci)을 A2에 넣을지 [결정 필요].
- A2는 벤치마크로 재지 않은 형식입니다. 적재 뒤 태그가 붙은 벤치마크 공고로 A1과 A2를 I5와 같은 방식으로 비교해 남깁니다 [제안].
- 성별·중성화·나이는 검색에서 필터로 처리합니다. 카드에서 뺄지는 효과를 재 보고 정합니다 [결정 필요]. 그 전까지는 벤치마크대로 넣어 둡니다.

### 10.2 변경분 판정

- **키**: (`desertion_no`, `text_kind`, `model_key`). `model_key = gemini-embedding-2:768` (사진과 같은 값)
- **해시**: 카드 문자열(공백 정리 후)의 `text_sha256`
- **다시 만드는 경우**
  - 카드에 들어가는 값이 바뀜 (해시가 달라짐). 태그가 나중에 붙거나 바뀌어도 그 공고만 다시 만듭니다.
  - `template_version`이 바뀜 → 전부 다시 만듭니다.
  - 모델이나 차원을 바꿈 → 사진과 함께 전부 다시 만듭니다 (글은 약 $1, 사진은 약 $4).
- **보내기 전**: 같은 `text_sha256` + `template_version` + `model_key`가 S3에 있으면 보내지 않습니다.
- `updTm`은 쓰지 않습니다. 사진처럼 내용으로 판정합니다 (data_contract 3절).
- 입양·반환으로 끝난 공고는 벡터를 지우지 않고, 검색할 때 공고 상태로 거릅니다 [제안].

### 10.3 저장 위치

**S3 (원본)**: `gold/text_embedding/model=<모델>/dim=768/template=<버전>/run_id=<id>/part-*.parquet`, `summary.json`

| 칼럼 | 내용 |
|---|---|
| `desertion_no`, `text_kind` | 키 |
| `text_sha256`, `template_version`, `model_key` | 변경 판정 |
| `text` | 임베딩한 카드 원문 (전화번호를 가린 뒤, 접두어 없이). 확인·디버깅용 |
| `tag_versions` | 비전 태그 모델·프롬프트 버전, Jev 태그 버전 (JSON). 어떤 태그 값으로 만든 카드인지 추적 |
| `embedding` | float32 × 768 |
| `source`, `embed_run_id`, `embedded_at` | 사진과 같음 (api·dup·reuse) |

`summary.json`에는 템플릿 칼럼 목록, `template_version`, 입력 접두어, 건수, 걸린 시간, 비용을 남깁니다.

**RDS (서비스용)**: 새 마이그레이션 `db/migrations/002_text_embedding.sql`(기존 파일은 고치지 않음, AGENTS.md 2번)로 `text_embedding` 테이블을 만듭니다.
- 칼럼: `desertion_no`, `text_kind`, `model_key`, `text_sha256`, `template_version`, `embedding vector(768)`, `embedded_at`
- 기본키: (`desertion_no`, `text_kind`, `model_key`)
- 인덱스: 코사인 HNSW (적재 뒤 db.t4g.micro에서 인덱스 없이 전체 비교한 시간과 비교해 정함)
- 스키마를 확정하면 `docs/data_contract.md`도 함께 고칩니다.

### 10.4 명령과 코드 (예정)

- 사진과 같은 순서: `embed-text --limit N [--dry-run]` → `staged --kind text` → 승인 → `load --kind text`(RDS `text_sha256`과 비교해 변경분만) → `smoke --kind text`
- 실행 위치: 사진과 같은 Pod. 모델을 서버에 올리지 않으므로 이미지가 가볍습니다. 매일은 03:00 수집 → 태깅 → 카드 → 글 임베딩 순서로 Airflow DAG에서 실행합니다.
- 코드는 `src/rebom/embedding/`에 둡니다.
  - 카드 만들기: `text_card.py` (A 템플릿 규칙 + 전화번호 가리기 + `template_version`)
  - 기존 모듈 재사용: `gemini.py`(글 접두어), `plan.py`·`staging.py`·`store.py`를 사진·글 공용으로 일반화
- smoke 항목 (기준 파일: Colab `cell_S1_글시험기준.py` 11-18판 → `text_smoke_ref.parquet`, 30마리)
  1. **카드 일치**: Silver 칼럼으로 만든 A1 카드(사회성 빼고 — `build_card`는 `soci=True`가 아니면 sfeSoci를 넣지 않음)가 기준 `text`와 글자까지 같은가 → 다르면 칼럼 매핑 문제. `silver_schema_check.py --card-ref text_smoke_ref.parquet`로 미리 확인 가능
  2. **벡터 일치**: 기준 `doc_input`을 Gemini 768로 임베딩한 값과 기준 `vec`의 코사인이 0.99 이상인가 → 다르면 모델 이름·차원·접두어 문제
  3. 카드 질의로 자기 공고 찾기
  4. 글 질의 지연 p95 (서버 Pod에서. Colab에서는 0.218초)

### 10.5 검색에서 합치는 방법 (모델선정 문서 8장 "검색 순위 규칙")

- 글 질의 → Gemini(질의 접두어) → `text_embedding`. 사진 질의 → Gemini(사진 전처리) → `image_embedding`.
- 두 목록은 **순위**로 합칩니다: `w글/(60 + 글 순위) + w사진/(60 + 사진 순위)`.
- 가중치: 첫 화면 0.6 : 0.4, 더보기 옆 토글로 "특징 중심" 0.7 : 0.3 · "생김새 중심" 0.4 : 0.6. C1 기준으로 잰 값이라 B로 다시 재서 바꿀 수 있습니다 (I3d). 이미 보여 준 공고는 뺍니다.
- 바깥 기준은 필터 단계입니다: 값이 맞음 → 미상 1개 → 미상 2개. 값이 다른 공고는 뺍니다.
- **글 질의로 사진 표도 찾기**: 같은 질의 벡터로 `image_embedding`도 검색해 세 번째 순위 목록으로 합칠 수 있습니다(추가 API 호출 없음). 외모 질의에서 도움이 되는지 I4로 잰 뒤 정합니다 [결정 필요].
- **API 장애**: 글·사진 질의 모두 Gemini를 부르므로, 응답이 약 2초 안에 없으면 필터 검색(SQL)만 보여 주고 안내합니다.

### 10.6 용량·비용 예상 (추정)

- 공고 약 18,854건 × 768차원: RDS 테이블 약 130MB, HNSW 약 60MB. 사진(약 130MB)과 합쳐도 `db.t4g.micro` 메모리 안에 들어갈 것으로 봅니다. 실측 뒤 확정합니다.
- API 비용: 3년치 첫 적재 약 $0.4~1.1 (카드당 100~300토큰 가정, 100만 토큰당 $0.20), 매일은 바뀐 공고(하루 약 200건)만 하므로 월 $0.1~0.4.
- 서버 메모리: 임베딩 모델을 올리지 않으므로 추가 없음.
- 템플릿을 바꾸면 전체를 다시 만들어야 하므로 템플릿 변경은 모아서 합니다. 이전 버전 벡터는 S3에 남겨 비교와 되돌리기에 씁니다.
