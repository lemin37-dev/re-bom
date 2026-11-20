# Silver 공고 칼럼 정리 요청 (Silver 담당 전달용)

작성: 2026-11-20 · 대상: Silver(정제) 담당 · 함께 볼 사람: 검색 API, 화면, 임베딩
상태: **제안. 다음 주 Silver 담당과 맞춘 뒤 확정**

> **한 줄 요약**
> 팀 칼럼 목록(노션)과 지금 Silver 코드(`storage/silver/transform_notice.py`)를 비교했습니다.
> ① 크기 경계는 `10kg 미만 소형`으로 코드가 바뀌었고, ② 보호소 정보는 변환 코드만 있고 **저장이 안 되며**,
> ③ 겹치거나 쓰지 않는 칼럼 7개를 정리하자는 제안입니다. 마지막 절에 다음 주에 함께 정할 것을 모았습니다.

---

## 1. 이미 반영한 것 (브랜치 `feat/front-notice-layout`)

| 항목 | 내용 |
|---|---|
| 크기 경계 | `weight_max <= 10` → **`< 10`** (11-20 결정). 규칙은 `cleaning/weight.py`의 `size_class()` 한 곳에만 있고, Silver와 검색 API가 함께 씁니다 |
| 크기 기준 | 개만. **10kg 미만 small · 10kg 이상 25kg 미만 medium · 25kg 이상 large**, 몸무게 없으면 null. 예: 9.9kg 소형, 10kg 중형, 24.7kg 중형, 25kg 대형 |
| 테스트 | `tests/storage/test_silver_schema_tags.py`: 10kg이 medium이 되도록 고치고 9.9kg, 24.7kg 경우를 추가 |
| 영향 | 샘플 200건 중 정확히 10kg인 7건이 small → medium. **이미 만든 Silver 데이터는 옛 기준 값이라 다시 만들어야 합니다** (Silver 담당이 실행) |

**팀 칼럼 목록(노션) 문구도 고쳐 주세요.** 지금 "10kg 이하 소형 · 10kg 초과 25kg 미만 중형"으로 되어 있어 코드와 다릅니다.

---

## 2. 저장이 안 되는 것: 보호소 정보

- `storage/silver/transform_shelter.py`에 보호소 변환이 이미 있습니다.
  `careRegNo → care_reg_no`, `careNm → care_name`, `careAddr → care_address`, `careTel → care_tel`, `orgNm → org_name`.
  같은 보호소 값이 서로 다르면 덮어쓰지 않고 충돌로 기록합니다.
- **하지만 이 함수를 부르는 파이프라인이 없습니다.** 그래서 수집 데이터에는 값이 있는데 Silver에는 저장되지 않습니다.
- 요청: 보호소 표(예: `silver.shelter`, 키 `care_reg_no`)로 저장하고, 공고 표의 `care_reg_no`로 잇습니다.
  공고 표에 보호소 칼럼을 반복해서 넣지 않습니다.
- 주소 범위: 지도에 쓸 위도·경도를 구하려면 **전체 주소가 필요합니다.** 보호소 주소는 기관 주소라 개인정보가 아닙니다.
  - 그래서 `care_address`(전체)를 저장하고, 화면 표시용 `care_sido`와 `care_sigungu`를 함께 두는 안을 제안합니다.
  - 팀 목록에는 "주소(시군구까지)"로 되어 있어 정해야 합니다(5절 ②).
- 보호자 실명 `careOwnerNm`은 지금처럼 넣지 않습니다(AGENTS.md).

---

## 3. 칼럼별 정리안

표시: ✅ 그대로 · ✏️ 바꿈 · ➖ 삭제 제안 · ⏸ 보류 · ➕ 추가 · 🔒 내부 전용

### 3.1 식별·날짜

| 지금 칼럼 | 팀 목록 | 제안 | 이유 |
|---|---|---|---|
| `desertion_no`, `notice_no`, `care_reg_no` | desertionNo, noticeNo, careRegNo | ✅ | |
| `happen_date`, `notice_start_date`, `notice_end_date` | happenDt·noticeSdt·noticeEdt (원문) | ✅ 날짜형 유지 | API 값이 `YYYYMMDD` 한 형식이라 날짜형으로 바꿔도 잃는 정보가 없습니다. 형식이 틀린 값은 null이 됩니다 |
| `notice_date` | — | ➖ | `notice_start_date`와 **같은 값**(둘 다 noticeSdt)입니다 |
| `source_updated_at_raw` | updTm | ✅ 🔒 | |

### 3.2 동물

| 지금 칼럼 | 팀 목록 | 제안 | 이유 |
|---|---|---|---|
| `species_code`, `species_name` | upKindCd, upKindNm | ✅ | |
| `breed_code`, `breed_name_raw` | kindCd, kindNm | ✅ | `breed_code`는 품종 정보 표의 `breed_key`와 다를 수 있습니다(팀 목록: 품종견만, 믹스는 null) |
| `breed_full_name_raw` | — | ✅ 🔒 | 검색 API가 `기타축종`의 이름을 이 값에서 읽습니다. 이번 범위가 개뿐이면 지워도 됩니다 |
| `is_mix` | is_mix | ✅ | 코드 000114·000219·000223·000115 또는 이름에 '믹스' |
| — | breed_key | ⏸ | 품종 정보 표를 쓸 때 추가 |

### 3.3 나이·몸무게·크기

| 지금 칼럼 | 팀 목록 | 제안 | 이유 |
|---|---|---|---|
| `age_raw`, `birth_year` | age, birth_year | ✅ | 화면은 **출생 연도만** 표시하고, 나이(살)는 쓰지 않습니다 |
| `u60_at_notice` | u60_at_notice | ✅ 🔒 | 화면에 쓰지 않습니다 |
| `age_note` | — | ➖ | 괄호 안 메모를 남기는 칸인데 원문 형식이 두 가지뿐이라 `u60_at_notice`와 겹칩니다 |
| `weight_raw`, `weight_kg` | weight, weight_kg | ✅ | `weight_kg` = 범위면 **큰 값**, g는 kg로, 100kg 초과는 null |
| `weight_min_kg`, `weight_max_kg` | — | ➖ | 범위값은 샘플 200건 중 7건(3.5%)이고 폭이 모두 0.5kg 이하입니다. API 원본 샘플 115건에는 0건입니다. `weight_kg`(= max)와 원문으로 충분합니다 |
| `weight_parse_status` | — | ✅ 🔒 | kg 값이 아니라 **어떻게 읽었는지 적은 꼬리표**(한 값, 범위, g, 없음, 애매함)입니다. 품질 점검용으로 남깁니다 |
| `size` | size | ✏️ | 경계는 1절. 표기(small 또는 소형)는 5절 ① |

### 3.4 털색

| 지금 칼럼 | 팀 목록 | 제안 | 이유 |
|---|---|---|---|
| `color_raw` | colorCd | ✅ | |
| `color_tag`, `color_pattern` | color_tag, color_pattern | ✅ | 필터와 표시에 씁니다 |
| `color_normalized`, `color_parse_status` | — | ➖ | "흰색"처럼 한 색으로 딱 떨어질 때만 값이 있고 나머지는 `needs_review`입니다. `color_tag`(5계열)가 같은 역할을 더 넓게 합니다 |

### 3.5 성별·상태·위치

| 지금 칼럼 | 팀 목록 | 제안 | 이유 |
|---|---|---|---|
| `sex_code`, `neuter_code` | sexCd, neuterYn | ✅ | M/F/Q · Y/N/U |
| `process_state_raw` | processState | ✅ | |
| `status_group` | — | ✅ | `PROTECTING`/`CLOSED`/`UNKNOWN`. 검색의 보호중 필터가 이 값을 씁니다 |
| `closure_reason_code` | — | ➖ (선택) | "종료(입양)"에서 '입양'을 뽑은 값이라 `process_state_raw`로 언제든 다시 만들 수 있습니다. 통계에 쓰면 남깁니다 |
| `end_reason_text` | endReason | ✅ | API의 처분 사유 원문 |
| `quality_warning` | — | ✅ 🔒 | 보호중 목록에서 왔는데 상태 값이 빈 공고 표시 |
| `happen_place` | happenPlace | ✅ 🔒 + ➕ | 원문의 절반쯤에 상세 주소(도로명 번호, 지번)가 들어 있습니다(샘플 44~62%). 원문은 내부용으로 두고, 화면용 잘린 칼럼을 추가합니다(5절 ⑤) |
| `org_name` | orgNm | ✅ | 지역 필터 |

### 3.6 정보 글

| 지금 칼럼 | 팀 목록 | 제안 | 이유 |
|---|---|---|---|
| `special_mark` | specialMark | ✅ | 화면 "공고 특징 · 원문" |
| `info_text` | info_text | ✏️ 순서 | 지금은 **sfeSoci → sfeHealth → specialMark** 순서로 공백 하나를 넣어 이어 붙입니다. 팀 기준은 **specialMark + sfeSoci + sfeHealth**라 순서를 바꿔야 합니다. 빈 값은 건너뜁니다 |
| — | sfeSoci, sfeHealth 원문 칼럼 | 추가하지 않음 | `info_text` 안에 합쳐져 있어 따로 두지 않습니다(11-20 결정). 각각 1.5%만 채워져 있습니다 |
| — | healthChk, vaccinationChk | ⏸ 확인 | 지금 상세 화면이 "예방접종·건강검진"을 보여 줍니다(9.9%·11.5% 채워짐). Silver에 없으면 db 모드에서 이 줄이 "확인 필요"로만 나옵니다. 칼럼을 추가할지, 화면에서 뺄지 정해 주세요(5절 ④) |
| `rfid_yn` | rfid_yn | ⏸ | 내부 보류. 지금 코드는 `rfidCd` 칸에 값이 있는지로 판단합니다. 화면에서 쓰지 않습니다. 번호(`rfidCd`)는 저장하지 않습니다 |

### 3.7 앞으로 추가될 칼럼 (만드는 곳이 정해짐)

| 칼럼 | 만드는 곳 | 내용 |
|---|---|---|
| `social_text` | Jev | `info_text`에서 뽑은 성격 정보(순함, 겁이 많음, 친화적 …)를 상위 태그로 묶은 값 |
| `health_text` | Jev | `info_text`에서 뽑은 건강 정보를 부위별로 태깅한 값 |
| `image_feature` | 비전 모델 | 사진에서 뽑은 특징(눈매, 품종, 귀, 털 길이) |
| `jev` | Jev | API 정보·시점 등 버전 구분 값 (json) |
| `tag_version` | 태깅 | 태그 채점 버전 |
| `embedding` | 임베딩 | 아래 4절 |

---

## 4. `embedding` 칼럼 (버전 정보 json) 제안

벡터 자체는 RDS `image_embedding`(사진)과 `text_embedding`(글)에 있습니다. Silver의 `embedding` 칼럼은 **이 공고 벡터가 어떤 버전으로 언제 만들어졌는지**만 적는 요약입니다.
지금 코드와 S3 기록에 이미 있는 값 중에서, 모델이나 전처리가 바뀌었을 때 **어느 공고를 다시 만들지** 가리는 데 필요한 것만 골랐습니다.

```json
{
  "model_key": "gemini-embedding-2:768",
  "photo": {
    "slots": [1, 2],
    "prep_version": "exif-rgb-max1024-jpeg90",
    "embed_run_id": "embed-<run_id>",
    "embedded_at": "2026-11-19T03:12:00+09:00"
  },
  "text": {
    "template_version": "A2",
    "text_sha256": "<64자리>",
    "embedded_at": null
  },
  "canary_reference_created_at": "2026-11-19T…"
}
```

| 항목 | 왜 필요한가 | 지금 어디에 있나 |
|---|---|---|
| `model_key` | 모델·차원이 바뀌면 다시 만들 대상 | 코드 `MODEL_KEY`, RDS `model`·`model_key` |
| `photo.slots` | 벡터가 있는 사진 칸. 사진 칸이 빠졌는데 옛 벡터가 남은 경우를 찾습니다 | RDS `image_embedding` |
| `photo.prep_version` | 전처리(크기, JPEG 품질)를 바꾸면 같은 사진도 벡터가 달라집니다 | S3 parquet·summary.json. **RDS에는 없음** |
| `photo.embed_run_id`, `embedded_at` | 언제 어느 실행에서 만들었는지. canary가 실패한 날 만든 벡터를 골라낼 때 씁니다 | S3 summary.json, RDS `embedded_at` |
| `text.template_version`, `text_sha256` | 글 카드 규칙(A1·A2)이나 내용이 바뀌면 다시 만들 대상 | RDS `text_embedding` (적용 전) |
| `canary_reference_created_at` | 매일 감시(canary)의 기준 벡터 시점. 기준을 다시 만들면 그 전후를 구분합니다 | S3 canary 기준 파일 |

- OpenRouter가 실제로 처리한 제공자(Google AI Studio 등)는 실행마다 달라질 수 있습니다. 공고별로 남기지 않고 실행 기록(summary.json의 `api_usage`)에만 남깁니다.
- 이 json은 RDS 표와 S3 기록에서 **만들어 채우는 값**입니다. 손으로 관리하면 실제 벡터와 어긋나므로 직접 고치지 않습니다.
- 임베딩 쪽 후속: RDS `image_embedding`에 `prep_version`과 실행 ID를 넣으려면 새 번호 마이그레이션이 필요합니다(적용은 승인 뒤). 지금은 S3 기록으로 대신합니다.

---

## 5. 다음 주 Silver 담당과 정할 것

| # | 정할 것 | 지금 상태 | 제안 |
|---|---|---|---|
| ① | `size` 표기 | Silver는 `small`/`medium`/`large`(영어), 카드와 화면은 소형/중형/대형 | Silver는 영어 코드를 유지하고, 화면 adapter(`frontend/src/lib/api.ts`)에서 한국어로 바꿉니다(**이미 반영**). 한국어로 저장하기로 하면 adapter만 고치면 됩니다 |
| ② | 보호소 정보 저장 | 변환 코드만 있고 저장 안 됨 | 2절: 보호소 표로 저장, 주소는 전체와 시·군·구를 함께 |
| ③ | `info_text` 순서 | sfeSoci → sfeHealth → specialMark | specialMark → sfeSoci → sfeHealth로 변경. sfe 원문 칼럼은 두지 않음 |
| ④ | healthChk, vaccinationChk | Silver에 없음 | 상세 화면에 계속 보여 줄지 정하고, 보여 준다면 원문 칼럼을 추가 |
| ⑤ | 발견 장소를 자른 칼럼 | `happen_place` 원문 그대로 저장, 검색 API와 화면도 원문 그대로 | 원문은 내부용으로 두고 `happen_place_area`(읍·면·동/구까지)를 추가합니다. 검색 API는 자른 값만 보냅니다. 그때까지 화면은 원문을 그대로 둡니다 |
| ⑥ | 삭제 칼럼 | 3절 ➖ | `notice_date`, `age_note`, `weight_min_kg`, `weight_max_kg`, `color_normalized`, `color_parse_status`, (선택) `closure_reason_code` |
| ⑦ | 크기 다시 계산 | 기존 Silver 데이터는 `<= 10` 기준 | 경계 변경 뒤 Silver 다시 실행 |
| ⑧ | 특징 글 속 번호 | `special_mark`에 지번 모양 13건(관리 번호일 수도 있음) | 화면에 원문이 그대로 나가므로 확인. 글 카드는 API로 보내기 전에 전화번호를 가리고 관리 번호를 지웁니다 |

---

## 6. 관련 자료

- 코드: `src/rebom/storage/silver/transform_notice.py`, `transform_shelter.py`, `src/rebom/storage/cleaning/weight.py`
- 칼럼 점검 도구(읽기만 함): `src/rebom/embedding/handoff_20261120/silver_schema_check.py`
- 화면 명세: `docs/front_notice_filter_spec_20261119.md` (v3.1)
- 원본 필드 설명: `docs/data_contract.md`, `docs/rescue_fields.md`
- 임베딩: `docs/embedding_model_openrouter.md`, `docs/image_search_similarity.md`
