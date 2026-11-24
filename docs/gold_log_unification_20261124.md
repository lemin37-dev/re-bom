# Gold 로그 통합: 결과 표는 따로, 로그는 하나로

작성: 2026-11-24 (3차 정리) · 대상: Gold 담당, 태깅(사진·글), 임베딩 · 상태: **방향 확정, 코드 구현함 (표 생성·복사·배포 전)**. `BAD_IMAGE` 이름 변경은 PR #73

> **한 줄 요약**
> 태그·사진 벡터·글 벡터의 **결과 표는 지금처럼 따로** 두고,
> 태깅·임베딩의 **로그는 `gold_inference_log`, `gold_model_request_log` 두 표로 합칩니다.** 구분은 `task`로 합니다.
> 11-23의 "로그는 묶음별로 따로" 결정을 **11-24에 바꿨습니다.**

근거 표시: **[실측]** AWS 표를 읽기만 해서 확인(11-24) · **[코드]** `origin/develop` 0771635와 원격 브랜치 · **[시험]** 로컬 카탈로그 시험 · **[추정]** 확인 필요

---

## 1. 목표 구조

```
결과 표 (따로 관리, 변경 없음)
├─ gold_animal_tag           사진 태그, 글 태그
├─ gold_image_embedding      사진 벡터
└─ gold_text_embedding       글 벡터 (최종 A2 카드)

공용 로그 (태깅 + 임베딩)
├─ gold_inference_log        무엇을 처리했고 결과가 어땠나   ← gold_embedding_inference_log 합침
└─ gold_model_request_log    모델을 언제 얼마에 불렀나       ← gold_embedding_request_log 합침

그대로 두는 것
└─ gold_embedding_canary     모델 변화 점검 결과 (요청 6행만 공용 요청 로그에 기록)
```

| 작업 | 결과 표 | 처리 로그 `task` | 요청 로그 `task` |
|---|---|---|---|
| 사진 태깅 | `gold_animal_tag` | `photo_trait`, `photo_breed` | 같음 |
| 글 태깅 | `gold_animal_tag` | `text_personality`, `text_health` | `text_jev` (한 번 호출로 두 작업) |
| 사진 임베딩 | `gold_image_embedding` | `image_embedding` | `image_embedding` |
| 글 임베딩 | `gold_text_embedding` | `text_embedding` | `text_embedding` |
| canary | `gold_embedding_canary` | 없음 | `image_embedding` (`gold_run_id`가 `canary-`로 시작) |

글 태깅은 두 로그의 `task`가 달라 `task`로 조인하면 빠집니다. 실행 단위는 `gold_run_id`로 잇습니다.

---

## 2. 결정과 남은 것

### 2.1 결정 (11-24)

| # | 항목 | 결정 |
|---|---|---|
| 1 | 로그 관리 | 태깅·임베딩 로그를 **공용 로그로 하나로 관리**. `task`와 식별키(`subject_type`, `subject_key`)로 구분 |
| 2 | 결과 관리 | 결과는 **작업별 표로 따로**. 글 임베딩도 결과는 `gold_text_embedding`, 로그는 공용 (현재는 쌓인 값 없음) |
| 3 | test 태깅 로그(`NO_PHOTO` 포함) | **이번 범위에서 제외.** 태깅 DAG가 자동화되어 prod로 넘어갈 때 별도로 계획 (5절은 그때 참고) |
| 4 | 임베딩 `FAILED` 이름 | **`BAD_IMAGE`로 변경** (코드·테스트 수정함, 6절) |
| 5 | `task` 조건 | 공용 로그를 읽고 교체하는 모든 코드에 넣음 |
| 6 | 구현 시작 시점 | PR #67 병합 후 (11-24 병합됨) |
| 7 | `batch_size` | 유지 (`request_scope`·`item_count`는 11-23 규칙에 따라 #58에서 되돌려짐) |

### 2.2 남은 것

| # | 항목 | 제안 | 함께 정할 사람 |
|---|---|---|---|
| 1 | 데이터 계약 13.3~13.5, `gold_schema_design.md`의 로그 설명 | 통합 코드와 함께 고침. Gold 담당 확인 필요 | Gold |
| 2 | 묶음 요청의 `subject_key` | 지금처럼 첫 번째 대상 + `batch_size` | Gold, 임베딩, 글 태깅 |
| 3 | 옛 임베딩 로그 표 삭제 시점 | 전환 뒤 1~2주 보관 후 사람이 삭제 | Gold, 임베딩 |

---

## 3. 왜 뷰가 아니라 표를 합치나

**Athena 뷰**는 두 표를 묶는 조회문에 이름을 붙여 저장한 것입니다. 한 번 만들면 한 표처럼 조회하지만, **실제 표는 여전히 2개**이고 조회할 때마다 안에서 두 표를 합칩니다.

```sql
CREATE VIEW gold_request_all AS
SELECT task, gold_run_id, requested_at, cost_usd FROM gold_model_request_log
UNION ALL
SELECT task, gold_run_id, requested_at, cost_usd FROM gold_embedding_request_log;

SELECT task, sum(cost_usd) FROM gold_request_all GROUP BY task;   -- 조회는 한 번
```

| | 표를 합치기 (결정) | Athena 뷰 | 지금처럼 |
|---|---|---|---|
| 조회 | 한 표 | 한 이름 (안에서는 두 표) | 매번 두 표를 합치는 조회문 작성 |
| Athena 밖(파이썬 코드)에서 | 한 표를 읽음 | **두 표를 따로 읽음** | 두 표를 따로 읽음 |
| 칸을 추가할 때 | 한 곳 | 두 표 + 뷰 | 두 표 |
| 이관 작업 | 필요 | 없음 | 없음 |
| 한쪽 변경의 영향 | 서로 영향받음 | 없음 | 없음 |

- 비용 조회만 목적이라면 뷰로도 됩니다.
- **로그 관리까지 하나로** 하려는 목적에는 표를 합치는 것이 맞습니다.
- prod 태깅 로그 표가 아직 없고 데이터가 작아 **지금이 바꾸기 가장 쉽습니다.**

11-21에 나눈 이유는 ① 한쪽 변경이 다른 쪽에 영향을 주지 않게 ② 임베딩만 쓰는 칸을 태깅 표에 더하지 않게 ③ 담당을 분명하게 하기 위해서였습니다. 합치면 이 장점을 포기하므로 7절의 조건을 지켜야 합니다.

---

## 4. PR #67 글 임베딩과의 관계 [코드]

PR #67(11-24 병합)은 최종 A2 카드의 벡터를 `gold_text_embedding`에 저장합니다. **이 부분은 그대로 둡니다.** 바꿀 것은 로그를 쓰는 곳뿐입니다.

| | PR #67 | 통합 뒤 |
|---|---|---|
| 글 벡터 | `gold_text_embedding` | **변경 없음** |
| 처리 로그 | `gold_embedding_inference_log` | `gold_inference_log` (`task = text_embedding`) |
| 요청 로그 | `gold_embedding_request_log` | `gold_model_request_log` (`task = text_embedding`) |

- 고친 위치: `embedding/text_batch.py`의 로그 표와 데이터 계약 13.4·13.5의 설명
- PR #67은 공통 요청 로그에 `version`도 더합니다. 통합 표에는 `batch_size`와 `version`이 함께 있어야 합니다.
- 순서: **PR #67을 먼저 합치고, 그 뒤에 로그를 통합**합니다. PR #72는 #67 위에 쌓여 있습니다. PR #67이 글 임베딩을 prod에 쓰기 전이면 옮길 글 임베딩 로그가 없습니다(현재 test·prod 모두 글 벡터 0행 [실측]).

---

## 5. test 태깅 로그의 `NO_PHOTO` 문제 (이번 범위 제외, prod 전환 때 참고)

### 무슨 뜻인가
`NO_PHOTO`는 "이 공고에는 사진이 없어서 태깅하지 않았다"는 기록입니다. 그런데 **실제로는 사진이 있는 공고에도 이 기록이 붙어 있습니다.**

```
공고 A
 ├─ 태깅이 읽은 Silver test 표          사진 칸은 있지만 S3 키가 비어 있음 → "사진 없음(NO_PHOTO)"으로 기록
 └─ 다른 Silver test 표(더 최근)        같은 공고에 S3 키가 있음           → 실제로는 사진이 있음
```

| 확인한 것 [실측] | 값 |
|---|---|
| `NO_PHOTO`로 기록된 공고 | 14,828건 (작업 2개라 로그는 29,656행) |
| 그중 다른 Silver test 표(`silver_animal_image_auto_b_test`)에 사진 키가 있는 공고 | **13,100건 (88%)** |
| 태깅이 읽은 표(`silver_animal_image_real_20261111_test`)에서 사진 키가 있는 공고 | 전체 20,420건 중 999건 |

태깅 코드의 오류라기보다, **시험용 입력 표에 사진 키가 일부만 채워져 있어** 생긴 기록으로 보입니다. [추정]

### 그대로 옮기면
prod 로그에 "사진 없는 공고 14,828건"이라는 **잘못된 기록**이 남아 통계가 틀어집니다. 이후 사진이 확인된 공고는 정상적으로 태깅되지만, `NO_PHOTO` 기록도 함께 남습니다. [코드]

### 제안
- prod에는 **`NO_PHOTO`를 옮기지 않습니다.** 태깅이 prod에서 처음 실행될 때 올바른 Silver로 새로 기록합니다.
- 실제로 태깅한 결과(사진 200장, 글 공고 200건)를 옮길지, 다시 만들지는 태깅 담당이 정합니다. 다시 만들면 시험과 같은 양 기준 약 $0.70입니다.
- 옮긴다면 로그만이 아니라 `gold_animal_tag`의 결과도 함께 옮깁니다. 로그만 옮기면 "끝남"으로 보고 다시 태깅하지 않아 태그가 비게 됩니다.

---

## 6. 실패 상태의 뜻

### 사진 임베딩의 실제 동작 [코드]

| 상황 | 로그 | 같은 실행에서 | 다음 날 |
|---|---|---|---|
| Bronze 파일을 못 읽음 | `READ_ERROR` | 재시도 안 함 | **다시 시도** |
| 받은 파일의 sha256이 다름 | `SHA_MISMATCH` | 재시도 안 함 | **다시 시도** |
| 모델 호출 오류 (429·5xx 등) | 사진 로그 없음 | 최대 5번 재시도, 안 되면 실행 중단 (Airflow가 task를 10분 뒤 1번 재실행) | 벡터가 없어 **다시 시도** |
| 사진 파일이 열리지 않음 | `BAD_IMAGE` (전 `FAILED`) | 재시도 안 함 | **다시 시도 안 함** (사진이 교체되면 새로 시도) |
| 빈 사진 | `BLANK` | 재시도 안 함 | **다시 시도 안 함** |

즉 "다음 날 다시 시도"는 `READ_ERROR`·`SHA_MISMATCH`와 모델 호출 오류에 해당하고, **`BAD_IMAGE`·`BLANK`는 같은 파일이면 다시 시도하지 않습니다.**

### 나중에 사진이 정상으로 바뀌면 [코드]
처리 기록은 **파일 내용(sha256) 단위**입니다. 내용이 1바이트라도 바뀌면 sha256이 달라져 새 사진으로 보냅니다.

| 상황 | 결과 |
|---|---|
| 공고의 **사진 주소가 바뀜** | 수집이 새 버전으로 다시 받음 → sha256이 달라짐 → **새로 임베딩** |
| **주소는 그대로인데 서버의 파일만 바뀜** | 수집은 이미 성공한 주소를 다시 받지 않음 → sha256 그대로 → **다시 시도하지 않음** |
| 파일이 그대로임 | 다시 시도해도 결과가 같음 → 시도하지 않음 |
| 빈 사진 기준이나 이미지 라이브러리를 바꿈 | 파일이 같아 **자동으로 다시 시도하지 않음.** 해당 기록을 사람이 정리해야 함 |

- 깨진 파일은 수집 단계가 먼저 거르고 다시 받습니다(끝까지 읽히지 않으면 Bronze에 저장하지 않음). 그래서 `BAD_IMAGE`는 드물고 **현재 0건**, `BLANK`는 3건입니다. [실측]
- 임베딩 중 이미지 처리에서 난 오류는 종류와 관계없이 `BAD_IMAGE`가 됩니다. 메모리 부족 같은 일시 오류도 여기에 들어갈 수 있으므로, `BAD_IMAGE`가 생기면 `last_error`를 확인합니다.

### 태깅과의 차이와 이름 변경

| | 태깅 | 사진 임베딩 |
|---|---|---|
| 다시 시도하는 실패 | `FAILED` (호출·답 오류, 총 3번까지) | `READ_ERROR`, `SHA_MISMATCH` |
| 다시 시도하지 않음 | `NO_RESULT`, `NO_PHOTO`, `NO_TEXT` | `BLANK`, **`BAD_IMAGE`** |

- 임베딩의 `FAILED`를 `BAD_IMAGE`로 바꿔, 공용 로그에서 `FAILED`는 **태깅의 시도 실패**만 뜻합니다(3번을 채우면 종료).
- prod·test 임베딩 로그에 `FAILED` 행이 **0건**이라 데이터 수정은 필요 없습니다. [실측]

---

## 7. 합치기 위한 조건

| # | 조건 | 이유 |
|---|---|---|
| 1 | 읽기·교체 조건에 **`task`를 반드시** 넣는다 | 다른 작업의 행을 읽거나 지우지 않게 |
| 2 | 처리 로그 키는 그대로 (`subject_type`, `subject_key`, `task`, `version`, `input_hash`) | `task`가 달라 같은 사진도 충돌하지 않음 |
| 3 | 요청 로그는 **추가만** 하고 요청 1번 = 1행 | 비용을 더하기만 하면 되게 |
| 4 | 새 칸은 비울 수 있게, 기존 칸의 이름을 바꾸거나 지우지 않는다 | 한쪽 변경이 다른 쪽을 깨지 않게 |
| 5 | 칸 추가는 **표 먼저, 코드 나중** | 칸이 없으면 값이 오류 없이 버려짐 |
| 6 | 공용 표의 칸을 바꿀 때는 Gold·태깅·임베딩이 함께 확인 | 여러 작업이 같이 쓰는 표 |
| 7 | test와 prod의 행을 한 표에 섞지 않는다 | 시험 기록이 운영 통계에 들어가지 않게 |

### 7.1 컬럼 [코드]
공통 칸의 이름·타입·순서와 파티션이 같습니다. 공용 표에 더할 칸은 2개입니다.

| 공용 표 | 지금 | 더할 칸 | 태깅 행의 값 |
|---|---|---|---|
| `gold_inference_log` | 11칸 | `detail` (판정 근거, 요청 연결) | 비움 |
| `gold_model_request_log` | 27칸 (develop 기준, PR #67은 `version` 포함 28칸) | `batch_size` (한 요청에 보낸 대상 수) | 비움 = 1장 |

`gold_model_request_log_test`에는 되돌려진 `request_scope`·`item_count` 칸이 남아 있습니다. 959행 모두 비어 있어 영향은 없습니다. [실측]

### 7.2 대상 식별
`subject_key`에는 **식별 코드만** 넣습니다. 현재 처리 로그의 모든 행이 이 규칙을 지킵니다. [실측]

| `subject_type` | `subject_key` | 쓰는 작업 |
|---|---|---|
| `animal` | 공고번호 (15자리) | 글 태깅, 글 임베딩, 사진 없음 기록 |
| `photo` | 사진 원본 sha256 (64자) | 사진 태깅, 사진 임베딩, canary |

- 사진 태깅과 사진 임베딩은 둘 다 `photo`라 **작업 구분은 `task`로** 합니다.
- 묶음 요청의 `subject_key`는 첫 번째 대상입니다. 처리 건수는 요청 로그가 아니라 **처리 로그에서** 셉니다.

### 7.3 `task` 조건을 넣을 곳 [코드]

| 위치 | 지금 | 바꿀 것 |
|---|---|---|
| `tagging/run_photo_tags.py` 읽기 | 조건 없음 (전체) | `task` = `photo_trait`, `photo_breed` (**반영함**) |
| `tagging/run_text_tags.py` 읽기 (`feat/tag-text-jev`) | `subject_type = animal` (글 임베딩도 읽게 됨) | `task` = `text_personality`, `text_health` (그 브랜치에서 반영 필요) |
| `embedding/gold_store.py` 읽기·교체 | `task` 있음 | 변경 없음 |
| `embedding/text_batch.py` 쓰기 | 키에 `task`를 포함한 upsert | 변경 없음 (표만 공용으로) |

지금도 키에 `task`가 있어 결과가 틀리지는 않습니다. 속도도 현재는 문제가 아니며(3만여 행에 0.5~0.8초 [실측]), 구분을 분명히 하고 데이터가 늘 때를 대비하는 것입니다.

---

## 8. 현재 상태 [실측]

### 8.1 표와 행 수

| 환경 | 표 | 행 |
|---|---|---|
| prod | `gold_embedding_inference_log` | 37,314 (OK 37,311 / BLANK 3) |
| prod | `gold_embedding_request_log` | 538 (임베딩 532, canary 6) |
| prod | `gold_embedding_canary` | 5 (모두 통과) |
| prod | `gold_inference_log`, `gold_model_request_log`, `gold_animal_tag` | **표 없음** |
| test | `gold_inference_log_test` | 31,594 (`NO_PHOTO` 29,656, 글 태깅 1,538, 사진 태깅 400) |
| test | `gold_model_request_log_test` | 959 (사진 태깅 800, 글 태깅 159) |
| test | `gold_embedding_inference_log_test` / `request_log_test` | 36,592 / 0 |
| 둘 다 | `gold_text_embedding` | 0 |

### 8.2 test와 prod
같은 DB(`rebom_gold`)에서 **표 이름(`_test`)과 S3 폴더만 다르고**, 같은 코드를 `--gold-env test|prod`로 나눠 실행합니다.

| | 임베딩 | 태깅 (현재) | 태깅 (예정) |
|---|---|---|---|
| 쓰는 곳 | prod | test | prod |
| 실행 | 매일 03:00 DAG | 사람이 실행 | DAG 자동 실행 |
| 입력 | Bronze 사진 manifest | Silver test | Silver prod |
| RDS | 사진 벡터 적재 중 | 없음 | 태그 표 새로 필요 (현재 마이그레이션 없음) |

### 8.3 기록된 비용

| 환경 | 작업 | 요청 | 비용(USD) |
|---|---|---|---|
| test | `photo_breed` | 400 | 0.386535 |
| test | `photo_trait` | 400 | 0.283440 |
| test | `text_jev` | 159 | 0.026095 |
| prod | `image_embedding` | 532 | 0.061765 |
| prod | canary | 6 | 0.000697 |

11-24 이전 임베딩은 요청별 기록이 없어 빠져 있습니다(각 실행의 `summary.json`에만 있음). `smoke` 점검과 비용이 돌아오지 않은 호출도 빠질 수 있어, 월 합계는 OpenRouter 청구와 대조합니다.

### 8.4 통합 뒤 비용 조회

```sql
-- 날짜는 한국 시간 기준. dt는 UTC라 03:00 실행이 전날로 잡힌다
SELECT date(requested_at AT TIME ZONE 'Asia/Seoul') AS day_kst,
       CASE WHEN gold_run_id LIKE 'canary-%' THEN 'canary' ELSE task END AS job,
       count(*) AS requests,
       sum(cost_usd) AS usd
FROM rebom_gold.gold_model_request_log
GROUP BY 1, 2
ORDER BY 1, 2;
```

---

## 9. 그 밖의 위험과 대응

| 위험 | 대응 |
|---|---|
| 태깅과 임베딩이 같은 표에 동시에 쓰면 커밋이 충돌. 로컬에서는 자동 재시도로 둘 다 성공했으나 [시험] **Glue에서는 미확인** | 전환 전 test 표에서 확인. 태깅 백필(약 6~7시간 예상)은 03:00 전후를 피함 |
| 옛 표로 되돌리면 전환 뒤 로그가 빠짐 | 복사 도구에 역방향 포함, 옛 표 1~2주 보관 |
| 다시 복사하다 새 기록을 덮어씀 | **없는 행만 추가**. 처리 기록은 같은 키에서 원본이 더 최신(`gold_updated_at`)일 때만 바꾸고, 대상이 더 최신이면 그대로 둠 |
| 공용 로그에 칸이 없는 채 배포되면 값이 오류 없이 버려짐 | canary·embed·load가 모델을 부르거나 쓰기 전에 칸을 확인하고 멈춤 |
| 표를 만들기 전에 배포되면 03:00 실행이 멈춤 (데이터는 안전, 새 사진 벡터만 늦어짐) | **표 생성·복사(4·5번)를 병합 전에** 끝냄 |

---

## 10. 진행 순서

| 순서 | 작업 | 담당 | 확인 |
|---|---|---|---|
| 1 | PR #67 병합 | Gold | ✅ 11-24 |
| 2 | 코드·테스트·문서: 공용 로그 사용, `task` 조건, 복사 도구 | 임베딩 (태깅 코드는 담당과 확인) | 전체 테스트 통과 |
| 3 | test 공용 로그에 칸 추가, 복사·동시 쓰기 시험 | 사람 실행 | 행 수·키·비용 일치 |
| 4 | prod 공용 로그 2개 만들기 | 사람 실행 | 표 있음 |
| 5 | prod 임베딩 로그 복사 | 사람 실행 | 아래 기준 |
| 6 | 03:00을 피해 병합·이미지 빌드, 직전에 5번을 한 번 더 | 사람 실행 | 빌드 완료 |
| 7 | 다음 03:00 실행 확인 | 임베딩 | 새 행이 공용 로그에만 추가됨 |
| 8 | 1~2주 뒤 옛 임베딩 로그 표 삭제 | 사람 실행 | Gold 합의 |
| 이후 | 태깅 DAG를 prod로 전환(5절 확인), RDS 태그 표 추가 | 태깅, 검색 | 별도 계획 |

**5번 통과 기준** (11-24 값, 실행 시점에 다시 잼)
- 처리 로그 37,314행, 키 중복 0, OK 37,311 / BLANK 3
- 요청 로그 538행, `request_id` 중복 0, 비용 합계 $0.062462
- 원본과 공용 표의 공통 칸 값 차이 0

하나라도 다르거나 공용 로그에 예상하지 못한 `task`가 있으면 전환하지 않습니다.

### 명령 (PC 관리자 프로필, 사람이 실행)

```
# 3. test: 칸 추가 → 복사 확인 → 복사
python -m rebom.common.gold_catalog --env test --create --group log --profile rebom
python -m rebom.embedding.log_copy --gold-env test --profile rebom
python -m rebom.embedding.log_copy --gold-env test --profile rebom --write

# 4·5. prod: 공용 로그 만들기 → 복사 확인 → 복사 (6번 직전에 마지막 줄을 한 번 더)
python -m rebom.common.gold_catalog --env prod --create --group log --profile rebom
python -m rebom.embedding.log_copy --gold-env prod --profile rebom
python -m rebom.embedding.log_copy --gold-env prod --profile rebom --write

# 되돌릴 때: 코드 revert 뒤 공용 로그의 임베딩 행을 옛 표로
python -m rebom.embedding.log_copy --gold-env prod --profile rebom --reverse --write
```

- `--write` 없이 실행하면 확인만 합니다. 예상하지 못한 `task`, 키 중복, 사라질 칸이 있으면 쓰지 않고 멈춥니다.
- 복사는 03:00 실행과 겹치지 않게 합니다.

### 바뀌는 파일

| 구분 | 위치 |
|---|---|
| 표 정의 | `common/gold_catalog.py` |
| 임베딩 | `embedding/gold_store.py`, `gold_migrate.py`, `cli.py`, `text_batch.py` |
| 태깅 | `tagging/run_photo_tags.py` (`run_text_tags.py`는 `feat/tag-text-jev`에서) |
| 새 파일 | `embedding/log_copy.py` (로그 복사 도구) |
| 테스트 | `test_gold_catalog_embedding.py`, `test_embedding_gold_store.py`, `test_embedding_gold_migrate.py`, `test_text_embedding_batch.py`, `test_embedding_log_copy.py`(새 파일) |
| 문서 | `data_contract.md` 13절, `gold_rules_embedding_review_20261120.md` 2.3 |

---

## 11. 확인한 것과 못 한 것

| 항목 | 결과 |
|---|---|
| AWS 표 읽기 (쓰기 없음) | 5절, 8절 |
| 공통 칸·파티션 비교 | 같음 |
| 로컬 통합 시험 | 같은 사진의 태깅·임베딩 공존, 임베딩을 다시 써도 태깅 행 유지 |
| `BAD_IMAGE` 변경 뒤 테스트 | 해당 PR에 기록 |
| 실제 규모 복사 시험 | prod 로그 37,314행·538행을 로컬로 읽어 와 복사. 차이 0, 비용 합계 $0.062462, 다시 실행해도 추가 0, 1.6초 |
| AWS에서 복사 도구 확인 모드 | 표·칸을 만들기 전이라 test(`detail` 칸 없음)·prod(표 없음) 모두 쓰지 않고 멈춤 = 의도한 동작 |
| Glue 동시 쓰기, AWS에 실제 복사 | **못 함** (쓰기가 필요) |
| 팀 노션의 Gold 확정 규칙 원문 | **못 봄** (접근 권한 없음). 브랜치·PR에 적힌 내용만 확인 |

관련: `docs/data_contract.md` 13절 · `docs/gold_rules_embedding_review_20261120.md` · PR #67 https://github.com/lemin37-dev/re-bom/pull/67
