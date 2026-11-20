# Gold 공통 규칙 초안 × 임베딩 검토 (v3)

작성: 2026-11-20 · 대상: Gold 담당(규칙 초안 작성자), 임베딩, 태깅 · 상태: **팀 결정 반영본 (Gold 담당 공유용)**
기준: Gold 공통 규칙 초안(11-20, 규칙 1~11), PR #51(공용 키 `subject_type`·`subject_key`, 11-20 develop 합침), 11-20 S3 실측

> **한 줄 요약**
> 임베딩 결과도 규칙 1에 따라 **Iceberg `rebom_gold`**로 옮깁니다. 태깅과는 **다른 결과 표 묶음**이고, **처리 기록·요청 기록은 태깅과 같은 공용 표**를 씁니다(PR #51).
> 칸 이름은 **이미 돌고 있는 태깅 표 이름**을 따릅니다. DAG 매일 실행은 **prod 표에만** 씁니다.
> 표 정의는 이번에 `common/gold_catalog.py`에 넣었고, 실제 쓰기 전환은 **11-21 DAG 첫 자동 실행을 확인한 뒤** 합니다.

v2와 달라진 점: PR #51로 공용 처리 기록이 사진 sha256을 대상으로 받게 되어, v2에서 제안한 임베딩 전용 처리 기록 표(`gold_embedding_item_log`)와 실행 표(`gold_embedding_run`)는 **만들지 않습니다.**

---

## 0. 지금 임베딩 기록은 S3 어디에 있나 (11-20 실측)

버킷 `rebom-data-559987919299`. **Glue·Athena에 등록되어 있지 않은 파일**입니다. 옮긴 뒤에도 지우지 않고 보관합니다.

| 무엇 | 위치 |
|---|---|
| 벡터 결과 | `gold/image_embedding/model=gemini-embedding-2/dim=768/run_id=<id>/part-*.parquet` (실행 4번, 42,820행, 약 110MB) |
| 실행 기록 | 같은 폴더 `summary.json` (건수, 빈 사진, 해시 불일치, 원본 읽기 실패, 비용·제공자) |
| canary 기준·결과 | `gold/image_embedding_canary/model=…/dim=768/{reference,reference_archive,checks/date=…,drift/date=…}` |
| 실행 로그 | `logs/airflow/dag_id=rescue_ingest/…/task_id=<check_embedding_drift·embed_photos·load_photo_embeddings>/` (11-21부터) |
| 서비스 사본 | RDS `image_embedding` (현재 사진 칸마다 1행, 변경분만) |

---

## 1. 결정 사항 (11-20 팀 논의)

| # | 내용 |
|---|---|
| 1 | **Iceberg로 옮김** (규칙 1). 바뀌는 값을 Iceberg merge(upsert)로 관리하고 Glue·Athena로 확인 |
| 2 | **표 묶음:** 결과 표는 태깅(Jev·비전)과 임베딩을 따로. 처리 기록·요청 기록은 공용(규칙 8, PR #51) |
| 3 | **실행 묶기:** 폴더가 아니라 표 안의 `gold_run_id` 칸 |
| 4 | **칸 이름:** 태깅 표에서 이미 쓰는 이름을 따름(2절). 규칙 초안의 이름과 다른 곳은 2절 표로 Gold 담당과 공유 |
| 5 | **사진당 1행** (규칙 2): 같은 사진을 쓰는 칸·공고는 벡터 1행을 같이 씀. 공고별로 볼 때는 현재 사진 정보와 sha256으로 합침 |
| 6 | **공고·칸 정보는 Gold 밖** (규칙 3): 결과 행에 `desertion_no`·`image_slot` 등을 두지 않음 |
| 7 | **모델 버전:** `model = "gemini-embedding-2:768"`. 이름의 숫자가 버전이고 `gemini-embedding-3`은 다른 모델. 같은 이름 안의 변화는 canary로 감시 |
| 8 | **글 카드 본문 남김:** 가린 카드 본문(`input_text`)을 글 임베딩 표에 남김. 글 벡터 성능 평가에 씀 |
| 9 | **빈 사진 점수 남김:** 공용 처리 기록에 `detail` 칸 추가 |
| 10 | **여러 사진을 한 요청으로 보낼 때 비용:** 사진마다 1행, 같은 `request_id`, `batch_size` 칸 추가, 비용·토큰은 나눠 넣음. `request_id`로 더하면 실제 값 |
| 11 | **test·prod 둘 다 만듦.** DAG 매일 실행은 prod에만 씀. test는 확인용 |
| 12 | **종료 공고:** 종료 뒤 **30일**이 지나면 RDS 벡터 삭제. Bronze도 30일로 맞추자고 수집 담당에게 제안 |
| 13 | **활성 버전:** 코드 상수 그대로. 바꾸면 PR로 변경 내역을 공유·기록 |
| 14 | **입력을 Silver 현재 사진으로:** Silver 사진 DAG가 정상으로 돈 뒤 (`--source silver`, `docs/image_embedding_status_20261120.md` 4절) |

---

## 2. 표와 칸 (Gold 담당 공유)

### 2.1 표 묶음

```
gold/iceberg/                  (test: gold/_test/iceberg/, 표 이름 뒤 _test)
├─ gold_animal_tag             태깅 결과
├─ gold_image_embedding        ┐
├─ gold_text_embedding         │ 임베딩 결과 (새로 만듦)
├─ gold_embedding_canary       ┘
├─ gold_inference_log          ┐ 공용 처리 기록 (+ detail 칸)
└─ gold_model_request_log      ┘ 공용 요청 기록 (+ batch_size 칸)
```

### 2.2 임베딩 결과 표

**`gold_image_embedding`**: 사진 1장 × 모델 × 전처리 버전이 1행. 파티션 `model`.

| 칸 | 값 예 | 키 |
|---|---|---|
| `subject_type` | `photo` | ✔ |
| `subject_key` | 사진 원본 sha256 | ✔ |
| `task` | `image_embedding` | ✔ |
| `model` | `gemini-embedding-2:768` | ✔ |
| `prompt_version` | `exif-rgb-max1024-jpeg90` (전처리 버전) | ✔ |
| `source_hash` | sha256 (사진은 `subject_key`와 같음) | |
| `embedding` | `list<float>` 768개 | |
| `gold_run_id`, `gold_updated_at` | | |

**`gold_text_embedding`**: 공고 글 카드 1개 × 모델 × 템플릿 버전 × 카드 해시가 1행. 카드가 바뀌면 새 행, 이전 행은 남음(규칙 5).

| 칸 | 값 예 | 키 |
|---|---|---|
| `subject_type` | `animal` | ✔ |
| `subject_key` | 공고번호 | ✔ |
| `task` | `text_embedding` | ✔ |
| `model` | `gemini-embedding-2:768` | ✔ |
| `prompt_version` | `A2` (글 템플릿) | ✔ |
| `source_hash` | 가린 카드 본문의 sha256 | ✔ |
| `input_text` | 가린 카드 본문 (전화번호 `[번호]`, 관리번호 지움) | |
| `source_update_dt` | 카드를 만든 Silver 행의 `source_updated_at_raw` | |
| `embedding`, `gold_run_id`, `gold_updated_at` | | |

- 지금은 Silver 칼럼 정리 전이라 비어 있습니다. 정리되면 템플릿으로 만든 카드가 여기에 쌓입니다.

**`gold_embedding_canary`**: 하루 한 번 고정 사진 6장 확인이 1행.
`gold_run_id`(`canary-<시각>`, 키), `task`, `model`, `prompt_version`, `checked_at`, `passed`, `threshold`(0.99), `min_cosine`, `photo_count`, `created_reference`, `reference_created_at`, `detail`(사진별 코사인, API 사용량 JSON), `gold_updated_at`.

### 2.3 공용 표에 임베딩이 쓰는 방식

**`gold_inference_log`** (키: `subject_type`, `subject_key`, `task`, `version`, `input_hash`)
- 사진: `subject_type = photo`, `subject_key = input_hash = sha256`.
- `version` = `<model>/<prompt_version>`, 예 `gemini-embedding-2:768/exif-rgb-max1024-jpeg90`. 이 표의 키에는 모델 칸이 없어서 모델까지 넣습니다. 이렇게 하지 않으면 모델을 바꿀 때 기록이 겹칩니다.
- `status`: `OK`, `BLANK`(빈 사진), `SHA_MISMATCH`, `READ_ERROR`(원본 못 읽음), `FAILED`.
- **새 칸 `detail`** (판정 근거 JSON, 태깅의 `gold_animal_tag.detail`과 같은 방식):
  - 빈 사진은 사진을 회색조 256×256으로 줄여 픽셀 밝기의 **표준편차**를 잽니다. 흰 대체 그림은 거의 한 색이라 0에 가깝고, 실제 사진은 이보다 훨씬 큽니다. 4.0 미만이면 빈 사진으로 봅니다.
  - 이 값을 `{"gray_std": 1.2, "blank_std": 4.0}`처럼 남기면, 나중에 기준을 바꿔도 사진을 다시 받거나 계산하지 않고 기록만 보고 다시 판정할 수 있습니다.
  - 태깅은 이 칸을 비워 둬도 됩니다(쓰기 코드가 표의 현재 스키마로 행을 만들어 빈 칸은 null).

**`gold_model_request_log`** (append)
- 사진 임베딩은 요청 1번에 사진을 최대 6장 보냅니다. 요청 1번을 1행으로만 남기면 그 비용이 어느 사진 몫인지 알 수 없습니다.
- 그래서 **사진마다 1행**을 남기고 같은 `request_id`를 씁니다.
  - **새 칸 `batch_size`** = 그 요청에 같이 보낸 사진 수. 비우면 1(태깅은 지금처럼 비움).
  - `cost_usd`·토큰은 사진 수로 나눈 몫을 넣고, 나머지는 첫 행에 붙입니다. `request_id`로 더하면 OpenRouter가 돌려준 실제 값과 같습니다.
- canary 요청도 같은 표에 `gold_run_id = canary-<시각>`으로 남겨, 감시 비용도 따로 볼 수 있게 합니다.
- 실행 하나의 건수·비용은 두 공용 표를 `gold_run_id`로 묶어 봅니다. 그래서 실행 표는 따로 만들지 않습니다.

### 2.4 칸 이름: 규칙 초안과 태깅 표 (Gold 담당 확인)

태깅 표는 이미 test에서 돌고 있어서 그 이름을 따랐습니다. 규칙 초안의 공통 칸 이름과 다른 곳은 아래와 같습니다. **규칙 초안을 태깅 표 이름으로 고치는 것**을 제안합니다.

| 규칙 초안 (규칙 7) | 태깅 표에서 쓰는 이름 | 임베딩 표 |
|---|---|---|
| 대상 키 (공고번호·sha256) | `subject_type` + `subject_key` | 같음 |
| `input_hash` | 결과 표 `source_hash`, 처리 기록 `input_hash` | 같음 |
| `model_version` | `model` | `model` = `gemini-embedding-2:768` |
| `prompt_or_prep_version` | 결과 표·요청 기록 `prompt_version`, 처리 기록 `version` | 결과 표 `prompt_version`, 처리 기록 `version` = `<model>/<prompt_version>` |
| `gold_run_id`, `gold_updated_at` | 같음 | 같음 |
| `source_update_dt` | (태깅 표에 없음) | 글 임베딩 표에 둠 |

기존 parquet 칸은 옮길 때 이렇게 바꿔 넣습니다.

| 지금 (parquet) | 새 표 |
|---|---|
| `sha256` | `subject_key`, `source_hash` |
| `embed_run_id` | `gold_run_id` |
| `embedded_at` (문자열) | `gold_updated_at` (시각 타입) |
| `prep_version` | `prompt_version` |
| 경로 `model=…/dim=…` | `model` (`gemini-embedding-2:768`) |
| `desertion_no`·`image_slot`·`photo_version`·`s3_key`·`photo_run_id`·`source` | 없음 (규칙 3, 사진당 1행) |

---

## 3. test와 prod

같은 DB `rebom_gold` 안에서 **표 이름과 S3 폴더만 다릅니다.**

| | test | prod |
|---|---|---|
| 표 이름 | `gold_image_embedding_test` 등 | `gold_image_embedding` 등 |
| S3 | `gold/_test/iceberg/` | `gold/iceberg/` |
| 누가 씀 | 사람이 확인할 때만 (옮기기 시험, Athena 조회, 벡터 저장 확인) | **DAG 매일 실행** (embed가 쓰고, load·canary가 읽음) |
| 지워도 되나 | 됨 (`--recreate`는 test만) | 안 됨 |

- 지금 서비스 RDS는 Iceberg 밖의 parquet에서 채워집니다. **전환 뒤 RDS의 원본은 prod 표**입니다.
- 임베딩 입력은 Bronze manifest라서 Silver prod가 열리기 전에도 Gold prod를 만들 수 있습니다.
- prod는 `--group embedding`으로 만들어 **임베딩 표와 공용 기록 표만** 만듭니다. 태그 표 prod는 태깅 담당이 정할 때 만듭니다.

---

## 4. 종료 공고 (규칙 10·11)

- 종료 뒤 **30일**은 RDS 벡터를 지우지 않고, 지나면 `load`가 지웁니다. 종료된 공고가 다시 보호중으로 돌아오는 경우가 있어서입니다.
- Bronze와 Gold에는 남아 있어, 지운 뒤 복귀해도 Gemini를 다시 부르지 않고 Gold에서 다시 넣습니다.
- soft delete 칸이 필요 없어 **RDS 마이그레이션이 필요 없습니다.**
- 사진 칸이 사라진 경우(규칙 11)도 같은 30일을 둡니다.
- 종료 날짜: 지금은 수집의 사라짐 이벤트(`silver/rescue/events/`), Silver 운영 뒤에는 Silver 상태 이력.
- RDS에서 지우는 일이라 **승인 뒤에 켭니다.**
- Bronze 보관도 30일로 맞추자고 수집 담당에게 제안합니다.

---

## 5. 글 입력 해시 (규칙 4): 이미 맞음

- 글 카드는 `build_card()` 안에서 전화번호를 `[번호]`로 가리고 앞의 관리 번호를 지운 뒤 템플릿(A2)으로 만듭니다.
- 해시는 이 가린 본문으로 만듭니다. 테스트로 고정했습니다(`tests/test_text_card.py::test_hash_is_of_masked_template_card`).
- 보호자 실명·연락처 칼럼은 카드를 만들 때 읽지 않습니다.

---

## 6. 규칙별 상태

표시: ✅ 지킴 · 🟡 표 정의 끝, 쓰기 전환 남음 · ⏳ 나중

| 규칙 | 상태 | 남은 일 |
|---|---|---|
| 1 Iceberg `rebom_gold` | 🟡 | 표 만들기(사람), 옮기기, 쓰기 전환 |
| 2 결과 키 | 🟡 | 사진당 1행, 키에 `model`·`prompt_version` |
| 3 연결은 Gold 밖 | 🟡 | 결과 행에서 공고·칸 칼럼 뺌 |
| 4 입력 해시 | ✅ | |
| 5 쌓기 | 🟡 | 같은 키 upsert, 지우지 않음 |
| 6 원본 출력·점수 | 🟡 | 빈 사진 점수 `detail` |
| 7 공통 칸 | 🟡 | 2.4절 이름 확인 |
| 8 실행 기록 공용 | 🟡 | 공용 표에 쓰기 (`version`, `batch_size`) |
| 9 활성 버전 한 곳 | ✅ | 코드 상수, 바꾸면 PR |
| 10 RDS 동기화 | ⏳ | 30일 뒤 삭제, 승인 뒤 |
| 11 칸 삭제 | ⏳ | 10과 같이 |

---

## 7. 순서

| 순서 | 작업 | 누가 | 상태 |
|---|---|---|---|
| 1 | 표 정의: 임베딩 표 3개, 공용 표 `detail`·`batch_size` (`common/gold_catalog.py`) | 임베딩 | ✅ 11-20, 로컬 Iceberg 테스트 통과 |
| 2 | 11-21 03:00 DAG 첫 자동 실행 확인 (canary, 809장, RDS 적재 건수) | 임베딩 | 11-21 오전 |
| 3 | 표 만들기 | **사람** | `--env test --create`, `--env prod --create --group embedding` (아래) |
| 4 | 기존 parquet → test 표로 옮기고 검증 (Gemini 호출 없음) | 임베딩 | sha256 35,369개, 벡터 비교, Athena 조회 |
| 5 | parquet → prod 표로 옮기기 | 임베딩 | 4와 같은 검증 |
| 6 | 코드 전환: embed는 prod 표와 공용 기록에 쓰고, load·canary는 prod 표를 읽음 | 임베딩 | PR 1개. parquet은 보관 |
| 7 | 종료 30일 뒤 RDS 삭제를 `load`에 추가 | 임베딩 | 승인 뒤 켬 |
| 8 | 입력을 Silver 현재 사진으로 | 임베딩 | Silver 사진 DAG 정상 운영 뒤 |

표 만들기 명령 (PC 관리자 프로필, 사람이 실행):
```
python -m rebom.common.gold_catalog --env test --create --profile rebom
python -m rebom.common.gold_catalog --env prod --create --group embedding --profile rebom
```
- test는 이미 있는 태깅 표 3개에 `detail`·`batch_size` 칸을 더하고 임베딩 표 3개를 만듭니다. 태깅 쓰기에는 영향이 없습니다.
- 옵션 없이 실행하면 확인만 합니다(있음/없음 출력).

---

## 8. 남은 확인

| # | 확인할 것 | 누구 |
|---|---|---|
| 1 | 2.4절 칸 이름: 규칙 초안을 태깅 표 이름으로 고치는 것 | Gold 담당 |
| 2 | 공용 표 칸 추가(`detail`, `batch_size`)와 처리 기록 `version` 형식 | Gold 담당, 태깅 |
| 3 | 파드(노드 역할)가 `rebom_gold` prod 새 표에 쓸 수 있는지 (test는 태깅이 쓰는 중) | 인프라 |
| 4 | Bronze 보관 30일 | 수집 |

---

## 9. 관련 자료

- 표 정의: `src/rebom/common/gold_catalog.py`, `docs/data_contract.md` 13.3·13.4절
- 임베딩 운영: `docs/image_embedding_pipeline.md`, 입력 전환: `docs/image_embedding_status_20261120.md`
- 모델·canary: `docs/embedding_model_openrouter.md`
- 테스트: `tests/test_gold_catalog_embedding.py`, `tests/tagging/test_gold_sink.py`
