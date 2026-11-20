# 사진 임베딩: 변경 사항과 대응 상황 (2026-11-20)

작성: 2026-11-20 · 대상: 팀 전체 (수집·DAG, Silver, 태깅, 검색 API, 화면) · 상태: **공유용 현황**

> **한 줄 요약**
> 사진 임베딩은 지금 **Bronze 사진 manifest를 직접 읽습니다.** 팀 방향은 "Silver 이미지 표에서 정제된 sha256과 S3 키를 읽는다"이고,
> Silver가 확정되어 운영(apply)으로 열리면 그때 넘어갑니다. 11-20에 실제 데이터를 대조해 보니 Silver(test)는 현재 사진의 **4.5%만** 담고 있어 아직 넘어갈 수 없습니다.
> 매일 임베딩은 11-20 오전 develop에 합쳐져 **11-21 03:00 실행부터** 돕니다. 그 사이 들어온 사진 809장은 그 실행에서 자동으로 처리됩니다.

---

## 1. 지금까지 바뀐 것

| 날짜 | 바뀐 것 | 자세히 |
|---|---|---|
| 11-17 | 사진 임베딩 파이프라인: Bronze manifest → 전처리 → Gemini → S3 parquet → (승인 뒤) RDS `image_embedding` | `docs/image_embedding_pipeline.md` |
| 11-18 | 사진·글 모두 모델 하나(`gemini-embedding-2`, 768차원)로 정함 | `docs/embedding_model_openrouter.md` 1절 |
| 11-19 | 서버 호출을 OpenRouter로 바꿈. 기존 벡터와 코사인 1.000000이라 다시 만들지 않음 | 같은 문서 3절 |
| 11-19 | 매일 감시(canary) 추가: 고정 사진 6장을 다시 임베딩해 0.99 미만이면 그날 임베딩을 멈춤 | 같은 문서 4절 |
| 11-19 | 검색 점수 = 공고의 사진 칸 중 **가장 높은 유사도**, 기본은 보호중 공고만. 종료 공고 벡터는 지우지 않음 | `docs/image_search_similarity.md` |
| 11-19 | Bronze 원본을 못 읽은 사진은 그 사진만 건너뛰고 다음 실행에서 다시 읽음 | `docs/image_embedding_pipeline.md` |
| 11-20 | Airflow `rescue_ingest`에 임베딩 task 연결이 develop에 합쳐짐 (PR #43) | 3절 |

---

## 2. 사진을 어디서 읽나: 지금과 앞으로

| | 사진 임베딩 (지금) | 사진 태깅 (지금) | 앞으로 (Silver 운영 뒤) |
|---|---|---|---|
| 현재 사진을 고르는 곳 | **Bronze manifest**를 모두 읽고 칸마다 최신 버전을 직접 고름 | **Silver 이미지 표** | Silver 이미지 표 (임베딩·태깅·화면이 같은 기준) |
| 사진 파일을 읽는 곳 | Bronze S3 원본 | Bronze S3 원본 (Silver에 적힌 키) | Bronze S3 원본 (Silver에 적힌 `original_s3_key`) |
| sha256 확인 | manifest 값과 비교 | Silver 값과 비교 | Silver 값과 비교 |

- 사진 파일 자체는 언제나 **Bronze S3에만** 있습니다. Silver 이미지 표는 그 파일의 키와 확인된 sha256을 칸마다 최신 정상 버전 하나씩 가진 표입니다.
- **공공 API 사진 주소(`source_url`)로 다시 접속하지 않습니다.** 오래된 공고 사진은 404가 나고, 같은 주소의 파일이 바뀔 수도 있습니다.
- Silver를 거치면 좋은 점:
  - 화면 썸네일·태깅·임베딩이 같은 "현재 사진"을 씁니다.
  - Silver는 S3 파일 정보의 sha256까지 한 번 더 확인합니다.
  - 업로드 중인 manifest(`in_progress`)는 보류합니다.

---

## 3. 11-20 실제 데이터 대조 (읽기만 함)

AWS `rebom` 프로필로 S3·Glue를 읽기만 했습니다. 쓰기·호출은 하지 않았습니다.

### 3.1 Bronze manifest와 Silver(test) 이미지 표

| 항목 | 값 |
|---|---|
| Bronze 사진 manifest | 11개, 모두 `ok` |
| Bronze 현재 사진 | **43,842칸** (공고 19,578건, 고유 sha256 36,175개) |
| Silver(test) 이미지 표 | 45,670행 (공고 20,420건). 공고의 `popfile` 주소로 만든 행 |
| 그중 S3 키와 sha256이 있는 칸 | **1,984칸 (Bronze 현재 사진의 4.5%)** |
| Silver 표 마지막 갱신 | 2026-11-18 09:56 (KST) |

- Silver에 키가 있는 1,984칸은 11-11 첫 두 사진 실행(20칸 + 1,964칸)에서 온 칸입니다. 백필 4번(약 3.8만 칸)과 11-17 이후 매일 실행분은 아직 반영되지 않았습니다.
- 두 쪽에 모두 있는 1,984칸 중 **1,978칸은 sha256과 키가 같습니다.** Silver에만 있고 Bronze에 없는 칸은 0입니다.
- **다른 6칸**은 모두 사진 교체(버전 2)입니다. Bronze에는 새 사진(`popfile1_v002.jpg`)이 있는데 Silver는 아직 옛 사진(`popfile1.jpg`)을 가리킵니다.
  - Silver의 사진 교체 반영이 "B 반영 전" 상태라서입니다(`docs/data_contract.md` 6절).
  - **이대로 넘어가면 옛 사진을 임베딩하게 됩니다.**

- 같은 결과를 `compare-sources` 명령으로 다시 확인했습니다(`switch_ready: false`).
- `embed --dry-run --source silver`로 보면 이 6칸은 **옛 사진의 벡터를 재사용**하게 됩니다(`reuse_vector: 6`). 그래서 Silver test로는 쓰기를 막아 두었습니다.

### 3.2 임베딩 결과 (S3 `gold/image_embedding/`)

| 항목 | 값 |
|---|---|
| 임베딩된 고유 sha256 | 35,369개 |
| 마지막 임베딩 실행 | 11-18 (`run_id=20261118T010033-70a32e`, 손으로 실행) |
| 아직 임베딩 안 된 현재 사진 | 812개. 빈 사진 3개는 원래 보내지 않으므로 **대상은 809개**: 11-19 사진 실행 399개, 11-20 실행 411개, 11-11 백필 2개 |
| canary 확인 기록 | 11-19 손으로 실행한 1건(`checks/date=2026-11-19/`)뿐. DAG 실행 기록은 아직 없음 |

- 이유: 임베딩 task가 DAG에 들어간 시점이 **11-20 09:51 develop 합침**입니다. 11-19와 11-20 03:00 실행에는 임베딩 task가 없었습니다. Airflow는 develop 브랜치의 DAG를 읽습니다(`infra/k8s/airflow/values.yaml`).
- 대응: 임베딩은 실행마다 **모든 manifest의 현재 사진**과 S3 결과를 비교해 빠진 것만 보냅니다. 그래서 **11-21 03:00 실행에서 809개를 자동으로 처리합니다**. 비용은 약 $0.09입니다(1장 $0.000116).
- 11-11 백필 2개는 이전 실행에서 원본을 읽지 못했을 수 있습니다. 확인하지 않았습니다. 11-21 실행 뒤에도 남으면 `summary.json`의 `read_error_keys`로 확인합니다.

---

## 4. Silver 입력으로 넘어가는 조건과 순서

**방향 (11-20 합의):** Silver가 확정되어 운영으로 열리면 임베딩 입력을 Silver 이미지 표로 바꿉니다. 그 전까지는 Bronze manifest를 그대로 씁니다.

**넘어가는 조건 (모두 충족해야 함):**

| # | 조건 | 지금 |
|---|---|---|
| 1 | Silver 운영 환경(prod) 표 이름·S3 경로 확정 | 막혀 있음 (`PROD_DISABLED`) |
| 2 | DAG의 Silver가 dry run이 아니라 실제 적재(apply)로 매일 돎 | `silver_dry_run` |
| 3 | 과거 사진 manifest(백필 포함 11개)가 모두 반영됨 | 11개 중 2개 (4.5%) |
| 4 | 사진 교체(버전 2 이상) 반영 | 반영 전 (6칸 차이) |
| 5 | Bronze 기준과 Silver 기준의 현재 사진 대조에서 차이 0 | 3.1절 대조를 다시 실행 |

**바꾸는 순서:**
1. ✅ 임베딩에 입력 선택 옵션 `--source bronze|silver`를 추가했습니다(11-20). 기본값은 bronze라 지금 동작은 바뀌지 않습니다.
   `--source silver --silver-env test`로는 S3·RDS에 쓰지 않고 `--dry-run`·`staged`만 됩니다.
2. 조건 1~4가 충족되면 대조 명령으로 차이가 0인지 확인합니다. 결과의 `switch_ready`가 `true`여야 합니다.
   `python -m rebom.embedding.cli compare-sources --silver-env <환경>`
3. DAG 순서를 바꿉니다. 지금은 사진 task가 끝나면 임베딩이 Silver와 따로 돌지만, 전환 뒤에는 **Silver task가 끝난 뒤** 임베딩이 돌아야 합니다.
4. DAG의 `embed_photos`에 `--source silver`를 넣습니다.
5. 첫 실행 뒤 `cli staged`로 결과를 확인합니다. 키가 sha256이라 기준만 같으면 다시 만들 벡터는 없습니다.

---

## 5. 대응 상황 한눈에

| 상황 | 영향 | 대응 | 담당 |
|---|---|---|---|
| 11-19·11-20 새 사진 809장 미임베딩 | 그 공고는 사진 검색에 안 나옴 | 11-21 03:00 DAG에서 자동 처리. 실행 뒤 Airflow 로그와 `summary.json` 확인 | 임베딩 |
| DAG 임베딩 첫 실행 (11-21) | canary·embed·load 첫 자동 실행 | `check_embedding_drift` 통과 여부와 RDS 적재 건수(`--max-write 5000`) 확인 | 임베딩, 수집·DAG |
| Silver(test)가 현재 사진의 4.5%만 가짐 | Silver로 바꾸면 사진 대부분이 빠짐 | Bronze 입력 유지. 4절 조건 충족 뒤 전환 | Silver |
| Silver가 사진 교체(v2)를 아직 반영 안 함 | 전환하면 옛 사진을 임베딩 | Silver 사진 교체 반영 뒤 전환 | Silver |
| 사진 태깅은 이미 Silver(test)를 읽음 | 태깅 대상도 Silver에 키가 있는 칸으로 좁혀짐 | Silver 운영 전환 때 함께 확인 | 태깅 |
| 종료 공고 벡터 유지 | 검색이 보호중을 거르지 않으면 종료 공고가 나옴 | 검색 API가 `status_group = PROTECTING`(또는 `processState = 보호중`)으로 거름 | 검색 API |

---

## 6. 다음 작업

| 작업 | 상태 |
|---|---|
| 임베딩 `--source bronze\|silver` 옵션 (기본 bronze), 대조 명령 `compare-sources` | ✅ 11-20 추가 (`src/rebom/embedding/sources.py`) |
| 결과 키(사진 단위·공고 단위)를 Gold Iceberg 표로 관리 | ✅ 11-20 표 정의(`common/gold_catalog.py`, 공용 처리·요청 기록 사용). 표 만들기·옮기기·쓰기 전환은 11-21 DAG 확인 뒤 (`docs/gold_rules_embedding_review_20261120.md` 7절) |
| 11-21 DAG 첫 자동 실행 결과 확인 | 11-21 오전 |

---

## 7. 관련 자료

- 임베딩 운영: `docs/image_embedding_pipeline.md`
- 모델·호출 경로·canary: `docs/embedding_model_openrouter.md`
- 검색 순위: `docs/image_search_similarity.md`
- 사진 manifest·교체 규칙: `docs/data_contract.md` 6절, 사진 태그: 13절
- Silver 칼럼 정리: `docs/silver_column_cleanup_20261120.md`
- 코드: `src/rebom/embedding/manifest.py`(Bronze 입력), `src/rebom/tagging/run_photo_tags.py`(Silver 입력 예), `src/rebom/storage/silver/environment_config.py`
