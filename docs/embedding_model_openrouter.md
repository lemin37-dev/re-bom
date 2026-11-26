# 임베딩 모델: gemini-embedding-2 + OpenRouter 전환

작성: 2026-11-20 · 대상: 팀 전체 (수집·DAG, 인프라, 정제, 검색 API, 화면) · 상태: **적용 완료** (PR #43, develop `8bc398d`)

> **한 줄 요약**
> 사진과 글 임베딩은 모두 `gemini-embedding-2`(768차원) 하나로 만들고, 서버에서는 **OpenRouter를 거쳐** 호출합니다.
> OpenRouter로 바꿔도 벡터가 Google 직접 호출과 **완전히 같아서**(코사인 1.000000) 이미 만든 벡터 42,820칸은 다시 만들지 않습니다.

---

## 1. 결정 사항

| 항목 | 값 |
|---|---|
| 모델 | `gemini-embedding-2` (OpenRouter 이름 `google/gemini-embedding-2`) |
| 차원 | 768 (`dimensions: 768`). 받은 벡터는 길이 1로 정규화(L2) |
| 대상 | 사진(지금 운영 중), 글(Silver 태그 칼럼이 정해진 뒤) — 같은 모델, 같은 벡터 공간 |
| 호출 경로 | 서버 운영 키가 OpenRouter 키(`sk-or-…`)라서 OpenRouter `POST /api/v1/embeddings` |
| 키 위치 | AWS Secrets Manager `rebom/embed/prod/gemini_api_key` (임베딩 전용 키). 코드·이미지·DAG에는 시크릿 **이름만** 있음 |
| 가격 | 글 100만 토큰당 $0.20, 사진 100만 토큰당 $0.45. 사진 1장 = 258토큰 = **$0.000116** |
| 결정일 | 모델 하나로 단순화 11-18 멘토링, OpenRouter 전환 11-19 |

**왜 모델 하나인가 (11-18)**: 사진·글을 같은 모델로 만들면 관리할 모델·키·버전이 하나로 줄고, 글 질의로 사진 벡터를 찾는 것도 추가 모델 없이 가능합니다.

**왜 OpenRouter인가 (11-19)**: 모델 비용을 실측하고 관리하기 위해 서버 운영용 API 키를 따로 받았고, 그 키가 OpenRouter 키입니다. 한 계정에서 사용량·비용·지출 상한을 볼 수 있습니다.

---

## 2. 코드가 고르는 경로

`src/rebom/embedding/gemini.py`가 키 모양을 보고 자동으로 고릅니다. 다른 코드는 바꿀 필요가 없습니다.

| 키 | 경로 |
|---|---|
| `sk-or-`로 시작 (서버 운영 키) | OpenRouter `https://openrouter.ai/api/v1/embeddings` |
| 그 밖의 키 (Google AI Studio 키) | Google SDK로 직접 |

- 사진은 전처리(EXIF 회전 → RGB → 긴 변 1024 이하 → JPEG 품질 90)한 결과를 `data:image/jpeg;base64,…`로 보냅니다. 요청 1번에 최대 6장.
- 실패 처리: 408·429·5xx는 다시 시도, 401(키 오류)은 바로 실패.

---

## 3. 바꿔도 되는지 확인한 결과 (2026-11-19)

| 확인 | 결과 |
|---|---|
| 사진 12장(여러 수집 실행에서 고름, png 원본 1장 포함) 1장씩 | 기존 벡터(Google 직접)와 코사인 **1.000000** (12/12) |
| 같은 12장을 6장씩 묶어서 | 1장씩 결과와 코사인 1.000000, 순서 12/12 일치 |
| 실제 코드 경로(시크릿 → OpenRouter) 6장 | 코사인 1.0 |
| 글 3문장 | 1개씩과 묶음 결과 코사인 1.0 |
| 응답 | 768차원, 길이 1.0, 응답 모델 이름 `gemini-embedding-2` |
| 비용 | 확인 전체 약 $0.004 |

→ **기존 S3·RDS 벡터 42,820칸을 그대로 씁니다.** 모델 이름 값도 `gemini-embedding-2` 그대로입니다.

---

## 4. 주의: OpenRouter의 버전 관리

- OpenRouter는 모델 이름을 고정해 주지만, **버전 번호를 따로 고를 수 없습니다.** 제공자(Google) 쪽에서 내부를 바꾸면 같은 이름으로도 벡터가 달라질 수 있습니다.
- 같은 이름을 **Google Vertex(us·global)·Google AI Studio** 중 한 곳으로 보냅니다. 지금까지는 어느 곳이든 벡터가 같았습니다.
- 벡터가 바뀐 줄 모르고 새 사진을 넣으면, 새 벡터가 기존 벡터와 다른 공간에 놓여 **검색이 조용히 망가집니다.**

### 그래서 매일 감시합니다 (canary)

Airflow `rescue_ingest` (매일 03:00): `photos_done → check_embedding_drift → embed_photos → load_photo_embeddings`

1. 매일 임베딩 전에 **고정 사진 6장**을 다시 임베딩합니다(약 $0.0007/일).
2. 6장 모두 저장해 둔 기준 벡터와 코사인 **0.99 이상**이면 → 그날 임베딩 진행.
3. 하나라도 미만이면 → **임베딩·적재를 멈추고** task 실패. 로그에
   "임베딩 벡터값이 변경되었습니다. 모델 버전 확인이 필요합니다."와 대응 방법이 나옵니다.
   그날 벡터는 S3에 모델 이름·날짜 경로로 저장합니다.
4. 기존 벡터는 그대로라 검색은 계속 됩니다. 새 사진 벡터만 늦어집니다.

**멈췄을 때 사람이 할 일**
1. Airflow에서 `check_embedding_drift`를 다시 실행(Clear) → 통과하면 일시 현상
2. 실패 기록의 제공자를 이전 기록과 비교 → 특정 제공자만 다르면 그 제공자를 고정
3. 모델이 실제로 바뀌었으면 → 전체 사진 다시 임베딩(약 $4, **승인 필요**) → 검색 품질 비교 → 전환 → `canary --reset`
4. 확인 전에는 task를 '성공'으로 표시하지 않기 (바뀐 벡터가 섞임)

> 실패 알림(Slack 등)은 내부 검토 중이라 아직 없습니다. Airflow 화면·로그로 확인합니다.

첫 실행(11-19): 6장 모두 코사인 1.0, 제공자 Google AI Studio, $0.000697, 12초.

---

## 5. 누가·어디서 썼는지 구분 (비용 관리)

OpenRouter 계정 하나에 여러 사용이 함께 찍힙니다. 기록의 제공자 이름(`Google`, `Google AI Studio`)은 실제로 처리한 곳이라 **바꿀 수 없습니다.** 그래서 요청마다 아래 표시를 붙입니다. 키·개인정보는 넣지 않습니다.

| 표시 | 값 | OpenRouter 기록에서 보이는 곳 |
|---|---|---|
| `user` | 환경 변수 `REBOM_USAGE_TAG`. DAG는 `rebom-embed:airflow:<task_id>`, 지정 안 하면 `rebom-embed:local` | `external_user` |
| `session_id` | 실행 단위: `embed-<run_id>`, `canary-<시각>`, `smoke-<시각>` | `session_id` |
| 앱 헤더 | `X-OpenRouter-Title: rebom-embed` (공개 순위에는 숨김) | `app_id` |

- 실제 API로 보낸 값이 기록에 그대로 남는 것을 확인했습니다(11-19). 기록이 조회되기까지 1분쯤 걸릴 수 있습니다.
- 우리 쪽 기록: 임베딩 실행마다 S3 `summary.json`의 `api_usage`에 비용(USD)·제공자·호출 수·표시값이 남습니다.
- **한계**: OpenRouter Activity 화면·CSV는 **모델·API 키·계정 구성원**으로만 나눠 볼 수 있습니다. 화면에서 바로 나눠 보려면 **용도·사람마다 키를 따로 만들어**(이름·지출 상한 지정) Secrets Manager에 각각 넣는 방법이 가장 확실합니다. 코드는 시크릿 이름(`REBOM_GEMINI_SECRET_ID`)만 바꾸면 됩니다.
- 로컬에서 실행할 때는 `REBOM_USAGE_TAG=rebom-embed:local:<이름>`처럼 본인 표시를 붙여 주세요.

---

## 6. 담당별로 알아 둘 것

| 담당 | 내용 |
|---|---|
| 검색 API | 질의(글·사진)도 OpenRouter를 부릅니다. **5초** 기다리고 실패하면 **2번** 다시, 모두 실패하면 필터 검색(SQL) 결과만 보여 줍니다 (`GeminiEmbedder(timeout=5, max_retries=2)`). 실측 사진 1장 p50 1.3초, 가끔 10초 이상 |
| 인프라 | 키는 Secrets Manager `rebom/embed/prod/gemini_api_key`만 씁니다. 용도별 키를 만들면 시크릿도 따로 만들어 주세요(값 변경은 사람이) |
| 정제(Silver) | 글 벡터도 같은 모델로 만듭니다. 태그 칼럼·특징 문장 칼럼 이름이 정해지면 알려 주세요 |
| 수집·DAG | DAG에 `check_embedding_drift`가 추가되었습니다. 실패하면 위 4절의 대응을 따릅니다 |
| 모두 | 키를 대화·코드·문서에 붙여 넣지 않습니다. `.env` 또는 Secrets Manager에서만 읽습니다 |

---

## 7. 관련 자료

- 코드: `src/rebom/embedding/gemini.py`(경로·표시), `canary.py`(감시), `cli.py`
- 운영 문서: `docs/image_embedding_pipeline.md` 7.1(canary), 7.2(비용 구분), 9.1(OpenRouter 확인)
- 글 벡터 계획: `docs/section10_Gemini.md`
- PR: https://github.com/lemin37-dev/re-bom/pull/43
