# 사진 임베딩 파이프라인: Bronze 사진 → S3 parquet → RDS pgvector

작성: 2026-11-17 · 상태: **시범 운영** (1,461칸 RDS 적재·검증 완료, 전체 백필 진행 중) · 코드: `src/rebom/embedding/`

표기는 `docs/data_contract.md`와 같습니다. 스키마·경로 약속은 그 문서 6절 "사진 임베딩 (pgvector)"이 기준이고, 이 문서는 실행 방법과 근거를 정리합니다.

## 1. 한눈에 보기

```
Bronze 사진 + manifest (S3, 수집 A가 매일 씀)
  │  manifest: bronze/rescue/image_runs/dt=<날짜>/run_id=<id>/manifest.json
  │  사진:     bronze/rescue/images/<desertionNo>/popfile<N>[_vNNN].<ext>
  ▼
① embed   보낼 사진 고르기 → 전처리 → Gemini(6장씩, 동시 4요청) → L2 정규화
  ▼
S3 parquet (원본, 모델·차원·전처리별로 모두 보관)
  │  gold/image_embedding/model=<모델>/dim=<차원>/run_id=<id>/part-*.parquet, summary.json
  ▼
② staged  S3 결과 점검 (과금 없음)          ── 사람이 확인·승인
  ▼
③ load    RDS sha256 과 비교해 변경분만 upsert
  ▼
RDS image_embedding (서비스용 사본, 서비스 중인 모델의 현재 사진만)
  ▼
④ smoke   사진 질의로 자기 자신 찾기·재임베딩 일치·지연 p95
```

| 단계 | 명령 | 읽는 곳 | 쓰는 곳 | 과금 |
|---|---|---|---|---|
| 점검 | `discover [--ping] [--db] [--sha-sample N]` | S3, Glue, Secrets, RDS | 없음 | 없음 |
| ① 임베딩 | `embed --limit N [--dry-run] [--workers 4]` | S3 manifest·사진·이전 결과 | S3 `gold/` | Gemini |
| ② 확인 | `staged [--run-id ID]` | S3 `gold/`, manifest | 없음 | 없음 |
| ③ 적재 | `load [--dry-run] [--full] [--run-id ID]` | S3 `gold/`, manifest, RDS | RDS | 없음 |
| ④ 검증 | `smoke [--sample N] [--trunc N]` | RDS, S3 사진 | 없음 | Gemini (N회) |

모든 명령은 `python -m rebom.embedding.cli <명령>`입니다.

## 2. 사용 자원

| 자원 | 값 |
|---|---|
| 모델 | `gemini-embedding-2`, `output_dimensionality=768` (지정하지 않으면 3072) |
| Gemini 키 | Secrets Manager `rebom/embed/prod/gemini_api_key` (서버 운영 전용 **OpenRouter 키**, 11-19 비용 실측용으로 분리, 9.1절. JSON이면 `GEMINI_API_KEY`·`gemini_api_key`·`api_key`·`key` 중 처음 있는 필드, 필드가 하나뿐이면 이름과 상관없이 그 값). 순서: 환경 변수 `GEMINI_API_KEY` → `.env` → Secrets Manager |
| 버킷 | `rebom-data-559987919299` (ap-northeast-2) |
| RDS | `rebom-dev-pg` PostgreSQL 18.3, DB·사용자 `rebom`, 접속 정보 `rebom/dev/rebom-db`, SSL 필수, k3s 노드에서만 접속 |
| pgvector | 0.8.1. 확장 설치는 관리자(`rds!db-...` 시크릿)만 가능 (아래 5절) |
| 테이블 | `image_embedding` (`db/migrations/001_init.sql`), 코사인 HNSW 인덱스 `image_embedding_hnsw_cos` |

환경 변수: `REBOM_S3_BUCKET`(필수), `AWS_PROFILE`·`AWS_DEFAULT_REGION`, `REBOM_GEMINI_SECRET_ID`, `REBOM_DB_SECRET_ID`, 포트포워딩 때 `REBOM_DB_HOST`·`REBOM_DB_PORT`. 이름은 `.env.example`에 있습니다.

## 3. ① embed: 보낼 사진 고르기와 전처리

### 3.0 현재 사진을 읽는 곳 (`sources.py`, 11-20)

- `--source bronze`(기본): 사진 manifest를 모두 읽어 아래 1번 규칙으로 칸별 현재 사진을 고릅니다.
- `--source silver`: Silver 이미지 표(칸마다 최신 정상 버전)의 `original_s3_key`·`sha256`을 그대로 씁니다. 사진 파일은 두 경우 모두 Bronze S3 원본에서 읽습니다.
- Silver가 운영으로 열린 뒤 넘어갑니다. 조건·순서: `docs/image_embedding_status_20261120.md` 4절. `--silver-env test`로는 S3·RDS에 쓰지 않습니다.
- 전환 전 대조: `python -m rebom.embedding.cli compare-sources --silver-env <환경>` → `switch_ready: true`

### 3.1 보내기 전에 거르는 것 (`plan.py`)

1. **칸별 최신 버전만**: (`desertionNo`, `slot`)마다 `version`이 가장 큰 항목, 같으면 나중 실행. `failed`는 대상 아님.
2. **이미 S3에 있는 칸 제외**: 같은 칸·같은 sha256이 S3 결과에 있으면 건너뜀.
3. **같은 sha256은 1번만**: 여러 칸·공고가 같은 파일을 쓰면 대표 1장만 보내고 나머지 칸은 벡터를 나눠 씀(`source=dup`). 이전 실행에서 만든 sha256이면 다시 보내지 않고 복사(`source=reuse`).
4. **빈 사진으로 판정된 sha256 제외**: `summary.json`의 `skipped_sha256`에 남겨 다음 실행에서도 보내지 않음.
5. **Bronze 파일을 못 읽은 사진은 다음 실행에서 다시** (11/19): 파일 없음(`NoSuchKey`)·권한·네트워크 오류로 원본을 못 읽으면 그 사진만 건너뛰고 `summary.json`의 `read_error_keys`(키 → 오류 코드)에 남깁니다. `skipped_sha256`에는 넣지 않으므로 다음 실행이 다시 읽습니다(사진 받기 실패를 다음 날 따라잡는 것과 같은 방식). `plan`의 `retry_read_errors`가 이번에 다시 시도하는 수입니다. 같은 파일을 3번 이상 연속으로 못 읽으면 로그에 `!!` 경고와 키를 남깁니다. 파일이 지워진 경우라 사진 수집에서 다시 받아야 합니다. 한 실행에서 20장 이상이면서 절반 넘게 못 읽으면 권한·네트워크 문제로 보고 종료 코드 4로 실패합니다(받은 결과는 S3에 남고 `load`는 돌지 않음). canary 기준 사진을 고를 때도 못 읽는 파일은 후보에서 뺍니다.
6. **`--limit`은 고유 sha256 수** 기준. 순서는 대표 칸의 (공고, 칸) 순이라 재실행해도 같습니다.

`--dry-run`이 출력하는 `plan` 항목:

| 항목 | 뜻 |
|---|---|
| `manifest_file_entries` | 모든 manifest의 `files` 항목 합 |
| `repeated_in_manifests` | 같은 칸·같은 버전이 다른 manifest에 다시 나온 수 (겹쳐 세기 확인) |
| `old_versions_dropped` | `_v00N` 교체로 밀려난 옛 버전 |
| `current_photos` / `unique_sha256` / `duplicate_photos` | 현재 칸 수 / 고유 파일 수 / 같은 파일을 쓰는 칸 수 |
| `new_keys_by_run` | 실행별로 처음 나온 칸 수 (사진이 늘어난 원인 확인) |
| `already_staged` / `reuse_vector` / `skip_known_blank` | S3에 이미 있음 / 벡터 복사 / 전에 빈 사진 |
| `to_send_sha256` / `this_run_sha256` | 남은 고유 사진 / 이번 실행에서 보낼 수 |

### 3.2 사진별 처리 (`prep.py`, 로컬 벤치마크 H1·I2와 같음)

1. S3에서 원본을 받아 **sha256을 manifest와 대조**. 다르면 보내지 않고 `sha_mismatch_keys`에 기록(일시 문제일 수 있어 다음 실행에서 다시 봄).
2. **빈 사진 판정**: 회색조 → 256×256 → 픽셀 표준편차 < 4.0이면 `blank`.
3. **전처리**: EXIF 회전 보정 → RGB → 긴 변 1024 이하로 축소(늘리지 않음) → JPEG 품질 90 → `image/jpeg`. 버전 이름 `exif-rgb-max1024-jpeg90`을 parquet에 남깁니다.
4. **요청**: 요청 1번에 최대 6장, 사진마다 Content 1개(문자열 목록을 그대로 넘기면 벡터 1개만 옴). 동시 요청 `--workers`(기본 4). 429·5xx는 지수 대기 후 최대 5번 재시도.
5. **L2 정규화**, 768차원이 아니면 중단.

### 3.3 S3 결과 (`staging.py`)

경로: `s3://rebom-data-559987919299/gold/image_embedding/model=gemini-embedding-2/dim=768/run_id=<UTC시각-6자리>/`

- `part-00000.parquet` …: 5,000행(`--part-size`)마다 저장. 중간에 끊겨도 저장된 부분은 남고, 재실행하면 이어서 진행.
- 칼럼: `desertion_no`, `image_slot`, `photo_version`, `sha256`, `s3_key`, `photo_run_id`, `embedding`(float32 × 768), `source`(api·dup·reuse), `embed_run_id`, `prep_version`, `embedded_at`. zstd 압축.
- `summary.json`: `status`(ok·aborted), `seconds`, `sec_per_sha256`, `stats`, `plan`, `parts`, `skipped_sha256`, `sha_mismatch_keys`.
- 다음 실행은 전처리 버전이 같은 행만 "이미 있음"으로 봅니다. 전처리를 바꾸면 전부 다시 만듭니다.

## 4. ②~④ 확인·적재·검증

**② staged**: 실행 목록, 행 수, 고유 sha256, 차원, 벡터 길이 범위, `source`별 수, 건너뛴 사유, 더 이상 현재가 아닌 행, `coverage`(현재 칸 중 S3에 있는 수).

**③ load**: manifest의 현재 sha256과 RDS의 sha256을 비교합니다.

| 출력 | 뜻 | 처리 |
|---|---|---|
| `new` | RDS에 없는 칸 | 씀 |
| `changed_sha256` | 사진이 바뀐 칸 | 덮어씀 |
| `unchanged_skip` | sha256이 같음 | 건너뜀 (`--full`이면 다시 씀) |
| `not_staged_yet` | S3에 아직 임베딩이 없거나 그사이 사진이 바뀜 | 건너뜀 |
| `rds_not_current` | RDS에 있지만 현재 사진이 아님 (교체 전·사라진 칸) | **세기만 함**, 지우지 않음 (아래) |

**RDS 행은 지우지 않습니다 [확정, 11/19]**: 종료(입양·반환 등)된 공고도 보호중으로 돌아올 수 있어(data_contract 3절, 복귀 4건 관찰) 벡터를 남겨 둡니다. 그래서 **검색이 기본으로 `processState = '보호중'`인 공고 안에서만** 찾도록 공고 상태와 함께 걸러야 합니다(검색 API 요구 사항). 복귀하면 다시 지우거나 만들 필요 없이 바로 검색에 나옵니다. 교체 전 사진은 같은 칸 키(`desertion_no`, `image_slot`, `model`)로 덮어써서 남지 않고, `rds_not_current`에는 새 벡터가 아직 없는 동안만 잡힙니다.

**공고별 사진 점수 [확정, 11/19]**: 한 공고의 모든 사진 칸(1~8)을 각각 질의 사진과 비교하고, **가장 높은 유사도를 그 공고의 점수**로 씁니다. 결과에는 공고마다 한 번만 나오고, 가장 닮은 칸(`best_slot`)을 함께 돌려줍니다(`store.nearest_notices`, `store.best_per_notice`). 첫 사진만 쓰면 1번 사진이 흐리거나 각도가 다를 때 놓치고, 평균은 흰 대체 사진처럼 관계없는 사진이 점수를 깎아 쓰지 않습니다. 같은 공고의 사진끼리는 로컬 시험에서 보통 유사도 0.9 이상이라 최고값과 다른 칸의 차이는 작습니다. 사진 칸을 먼저 200개 뽑은 뒤 보호중이 아닌 공고를 빼고 공고별로 묶습니다. 화면 썸네일을 1번 사진으로 둘지 `best_slot`으로 둘지는 화면 쪽에서 정합니다(점수를 낸 사진과 썸네일이 다를 수 있음).

500행씩 `INSERT … ON CONFLICT (desertion_no, image_slot, model) DO UPDATE`. `--full`은 전처리·벡터를 고쳤을 때만 씁니다.

**④ smoke**: ① RDS 행 수·차원·벡터 길이·manifest 대조, ② 사진 N장을 다시 임베딩해 질의 → top-1이 같은 sha256인지(같은 공고 여러 칸에 같은 파일이 있으면 유사도 1.0 동점이라 sha256으로 판정), ③ 재임베딩 벡터와 저장 벡터 코사인 ≥ 0.99, ④ (`--trunc`) 3072차원 앞 768개와 768 출력 비교, ⑤ 질의 지연 p50·p95(임베딩 호출 + pgvector 검색).

## 5. 처음 한 번 하는 일 (사람이 실행)

AGENTS.md 5번에 따라 DB 스키마 변경은 사람이 실행합니다.

1. **RDS 터널** (내 PC, 창 하나를 켜 둠):
   ```powershell
   aws ssm start-session --profile rebom --region ap-northeast-2 --target i-00d51057503809599 --document-name AWS-StartPortForwardingSessionToRemoteHost --parameters "host=rebom-dev-pg.c96qgackku5d.ap-northeast-2.rds.amazonaws.com,portNumber=5432,localPortNumber=15432"
   ```
   `Waiting for connections...`가 나오면 열린 것입니다. `--document-name`은 AWS 공용 문서 이름 그대로입니다.
2. **pgvector 확장**: RDS는 `rds_superuser`만 만들 수 있어 `rebom` 사용자로는 `permission denied to create extension "vector"`가 납니다. 관리자 시크릿(`rds!db-...`)으로 `CREATE EXTENSION IF NOT EXISTS vector;`만 한 번 실행합니다. (`infra/terraform/database/README.md`에는 `rebom` 사용자가 만들 수 있다고 적혀 있어 인프라 담당 확인이 필요합니다.)
3. **마이그레이션**: `rebom` 사용자로 `db/migrations/001_init.sql` 실행. `IF NOT EXISTS`라 다시 실행해도 안전합니다.

## 6. 실행 예 (내 PC, PowerShell)

```powershell
$env:AWS_PROFILE="rebom"; $env:AWS_DEFAULT_REGION="ap-northeast-2"; $env:REBOM_S3_BUCKET="rebom-data-559987919299"; $env:PYTHONIOENCODING="utf-8"
# RDS 를 쓰는 load·smoke 때만 (터널 필요)
$env:REBOM_DB_HOST="127.0.0.1"; $env:REBOM_DB_PORT="15432"

.venv\Scripts\python -m rebom.embedding.cli embed --limit 1000 --dry-run   # 계획만
.venv\Scripts\python -m rebom.embedding.cli embed --limit 1000              # S3 에 씀
.venv\Scripts\python -m rebom.embedding.cli staged                          # 확인 → 승인
.venv\Scripts\python -m rebom.embedding.cli load --dry-run                  # 변경분 수
.venv\Scripts\python -m rebom.embedding.cli load
.venv\Scripts\python -m rebom.embedding.cli smoke --sample 20 --trunc 0
```

- `aws login` 자격증명을 쓰려면 로컬 `.venv`에 `botocore[crt]`가 필요합니다. 서버 Pod는 노드 역할을 쓰므로 필요 없습니다.
- 설치: `pip install -e ".[embed]"` (google-genai, boto3, psycopg, Pillow, pyarrow).

## 7. 매일 운영 (변경분만)

Airflow `rescue_ingest` DAG(매일 03:00)에서 사진 수집이 끝나면 바로 이어서 돕니다. Silver와는 따로 가서 서로 막지 않습니다.

```
rescue_incremental → rescue_photos → photos_done ─┬→ silver_dry_run (이벤트와 함께)
                                                  └→ check_embedding_drift → embed_photos → load_photo_embeddings
```

0. `check_embedding_drift` = `canary`: **모델 변화 감시** (7.1). 통과해야 다음 단계로 갑니다.
1. `embed_photos` = `embed --limit 5000`: 모든 manifest의 현재 사진 중 S3 결과에 없는 sha256만 보냅니다. 같은 파일이 이미 있으면 호출 없이 복사합니다. run_id를 받지 않으므로, 사진 task가 일부 실패해도 받은 만큼만 하고 나머지는 다음 날 따라잡습니다. 5000은 상한입니다(약 $0.6, 평소 하루 수백 장·1분 안팎).
2. `load_photo_embeddings` = `load --max-write 5000`: RDS sha256과 다른 칸만 씁니다. 쓸 행이 상한을 넘거나 차원·정규화가 틀리면 **쓰지 않고 실패**합니다. 이때는 사람이 `staged`로 확인한 뒤 손으로 `load`를 실행합니다. `embed`가 실패하면 `load`는 돌지 않습니다.
3. 결과 확인은 Airflow 로그(`plan`, `load plan` 출력)와 S3 `summary.json`으로 합니다. `smoke`는 매일 돌리지 않습니다(과금).

- 이미지: ECR `rebom/embed` (`infra/docker/embed.Dockerfile`, develop 머지 시 CI가 빌드). 파드는 `rebom-ingest` 네임스페이스에서 뜹니다.
- 키: Gemini(`rebom/embed/prod/gemini_api_key`, 임베딩 전용)·DB(`rebom/dev/rebom-db`)는 노드 역할로 Secrets Manager에서 읽습니다. 이미지·DAG에 키를 넣지 않습니다.
- 메모리: S3 결과 전체(4만+ 행 × 768)를 읽습니다. 벡터를 float32 배열로 읽어 수백 MB 안에 들어갑니다(limit 2Gi, 실측 뒤 조정).
- 처음 배포 순서(사람): ① `infra/terraform/registry`에 `rebom/embed` 저장소 추가 apply ② develop 머지 → CI가 이미지 빌드 ③ Argo CD가 DAG 반영. 이미지가 없으면 `embed_photos`가 이미지 받기 오류로 실패하지만 Silver에는 영향이 없습니다.

### 7.1 모델 변화 감시 (canary)

모델 이름(`gemini-embedding-2`)이 같아도 제공자 쪽에서 내부가 바뀌면, 새로 만든 벡터가 기존 42,820칸과 다른 공간에 놓여 검색이 조용히 망가집니다. 자동화 안에서 이를 잡기 위해 매일 `embed` 전에 확인합니다.

- **고정 사진 6장**: 처음 실행 때 S3 결과 중 API로 만든 행을 sha256 순으로 6장 골라 기준으로 저장합니다. 전처리된 JPEG와 그때의 벡터를 함께 저장하므로, 매일 같은 바이트를 보냅니다(요청 1번, 약 $0.0007/일).
- **판정**: 6장 모두 기준 벡터와 코사인 **0.99 이상**이면 통과 → `embed`·`load` 진행. 하나라도 미만이면 종료 코드 3으로 **실패** → 그날 `embed`·`load`는 돌지 않습니다(기존 S3·RDS 벡터는 그대로, 새 사진 벡터만 늦어짐).
- **실패하면 저장·안내**: 이번 벡터를 모델 이름·날짜 경로(`drift/date=YYYY-MM-DD/`)에 저장하고, Airflow 로그에 "임베딩 벡터값이 변경되었습니다. 모델 버전 확인이 필요합니다."와 대응 방법을 출력합니다.
- **기록**: 매번 결과(코사인, 제공자, 응답 모델 이름, 비용)를 `checks/date=…/`에 남깁니다.

S3 경로 (`gold/image_embedding/` 밖에 둡니다. `load_staged`가 그 아래 파일을 모두 읽기 때문):

```
gold/image_embedding_canary/model=gemini-embedding-2/dim=768/
  reference/reference.json, reference/<sha256>.jpg   기준 (처음 실행 때 자동 생성)
  reference_archive/<시각>/reference.json            --reset 전 기준 보관
  checks/date=YYYY-MM-DD/<HHMMSS>.json               매일 결과
  drift/date=YYYY-MM-DD/<HHMMSS>.json                실패한 날의 벡터 (모델 이름·날짜)
```

실패했을 때 대응 (사람):

1. **일시 현상인지**: Airflow에서 `check_embedding_drift`를 다시 실행(Clear)합니다. 통과하면 그대로 진행됩니다.
2. **제공자 차이인지**: drift 파일의 `api_usage.providers`·`served_models`를 이전 `checks/`와 비교합니다. OpenRouter는 같은 이름을 Google Vertex(us·global)·Google AI Studio 중 한 곳으로 보냅니다. 특정 제공자만 다르면 `gemini.py`에서 provider를 고정합니다(`order` + `allow_fallbacks: false`).
3. **모델이 실제로 바뀌었으면**: 새 모델 키로 전체 사진을 다시 임베딩합니다(약 $4, 승인 필요). 검색 품질을 비교한 뒤 검색 API를 전환하고, 다시 임베딩이 끝나면 `canary --reset`으로 기준을 새 벡터로 바꿉니다(이전 기준은 `reference_archive/`에 보관).
4. 확인 전에는 이 task를 Airflow에서 '성공'으로 표시하지 않습니다. 표시하면 바뀐 벡터가 기존 벡터와 섞입니다.

첫 실행 (2026-11-19, 내 PC → OpenRouter): 기준 6장 생성, 6장 모두 코사인 1.0, 제공자 Google AI Studio, 응답 모델 `gemini-embedding-2`, $0.000697, 12초.

### 7.2 OpenRouter 비용 구분 (누가·어디서·어느 실행)

OpenRouter 계정 하나에 여러 사용이 함께 찍힙니다. 기록의 provider 이름(`Google`, `Google AI Studio`)은 실제로 처리한 곳이라 바꿀 수 없습니다. 그래서 요청마다 아래 표시를 붙입니다(키·개인정보는 넣지 않음).

| 표시 | 값 | OpenRouter 기록에서 |
|---|---|---|
| `user` | `REBOM_USAGE_TAG` (DAG: `rebom-embed:airflow:<task_id>`, 지정 안 하면 `rebom-embed:local`) | 생성 기록 `external_user` |
| `session_id` | 실행 단위: `embed-<run_id>`, `canary-<시각>`, `smoke-<시각>` | 생성 기록 `session_id` |
| 앱 헤더 | `HTTP-Referer`(레포 주소), `X-OpenRouter-Title: rebom-embed`, 공개 순위에서 숨김 | 생성 기록 `app_id` |

확인 (2026-11-19): 임베딩 요청 2건의 `GET /api/v1/generation?id=…`에서 `external_user`·`session_id`가 보낸 값 그대로, `app_id` 같은 값. 기록이 조회되기까지 1분쯤 걸릴 수 있습니다.

- 우리 쪽 기록: `embed`의 `summary.json`, `canary`의 `checks/` 파일에 `api_usage`(비용 USD·제공자·응답 모델·`usage_tag`·`session_id`)가 남습니다.
- OpenRouter Activity 화면·CSV는 **모델·API 키·계정 구성원**으로만 묶을 수 있습니다(`user`·`session_id`로는 못 묶음). 화면에서 바로 나눠 보려면 **용도·사람마다 API 키를 따로 만들고**(이름·지출 상한 지정) Secrets Manager에 각각 넣는 방법이 가장 확실합니다. 코드는 `REBOM_GEMINI_SECRET_ID`로 시크릿 이름만 바꾸면 됩니다.
- `user`·`session_id`로 집계하려면 관리 키(management key)로 Analytics API를 쓰거나 생성 기록을 조회해야 합니다 [미구현].

## 8. 설계 원칙: S3가 원본, RDS는 서비스용 사본

- **S3**: 모델·차원·전처리별로 모든 벡터를 남깁니다. 모델을 바꿔도 지우지 않아, 되돌리기·재적재에 Gemini를 다시 부르지 않습니다.
- **RDS**: 서비스 중인 모델의 현재 사진 벡터만 둡니다.
- **모델 교체 순서**: 새 모델 벡터를 S3에 만듦 → RDS에 나란히 넣어 비교(기본키에 `model` 포함, 차원이 다르면 새 마이그레이션) → 전환 → 옛 모델 행을 RDS에서 삭제. RDS에는 많아야 2개 모델만 잠깐 둡니다.
- **비용**: 디스크(gp3 20GB, 이미 할당)보다 **인스턴스 메모리(`db.t4g.micro`, 1GB)**가 제약입니다. HNSW 검색은 인덱스가 메모리에 있어야 빠릅니다. 768차원 이미지 1개 모델(인덱스 약 130MB)은 들어가지만, 모델 2개를 오래 두거나 이미지·텍스트를 함께 두면 빠듯합니다.
- **차원**: 768 유지. 3072는 용량 4배이고 pgvector HNSW 한도(2,000차원)를 넘습니다. smoke ④에서 3072의 앞 768개가 768 출력과 같았으므로(코사인 1.0) 나중에 필요하면 다시 받으면 됩니다.
- **텍스트 임베딩**: 같은 구조, 같은 모델(`gemini-embedding-2`, 768)로 만듭니다(10절, 11-18 결정: 모델 하나로 단순화). 검색에서는 글·사진 결과를 순위로 합칩니다. 카드·태그가 바뀌어 사진보다 자주 다시 만들어야 하므로 변경분 관리의 효과가 더 큽니다.

## 9. 실측 기록 (2026-11-17)

| 항목 | 값 |
|---|---|
| Bronze 사진 | 42,248개 (jpg 40,853 · 13.45GB, png 1,395 · 0.57GB), 공고 18,854건 |
| 현재 칸 / 고유 sha256 / 중복 칸 | 42,219 / 34,856 / 7,363 (17.4%). 다른 공고와 같은 파일 177개 |
| 오전 대비 +406칸 | 10:51 수집 실행(`20261117T105112-42b2b8`)에서 처음 나온 칸. manifest 겹쳐 세기 0 |
| sha256 표본 대조 | 30장 모두 일치 |
| 속도 | 100장 0.115초/장, 1,000장 0.102초/장 (6장씩·동시 4요청, 내 PC) |
| 시범 결과 | 고유 1,100장 → 1,461칸 (dup 361), 빈 사진 0, sha256 불일치 0 |
| RDS 적재 후 smoke | 1,461행 768차원·길이 1.0, manifest 불일치 0, 자기 자신 찾기 20/20, 재임베딩 코사인 1.0, 질의 p50 1.04초·p95 1.30초 (SSM 터널 포함) |
| 3072 앞 768 vs 768 | 코사인 1.0 (3장) |
| RDS 용량 | 1,461행: 테이블 10.4MB, HNSW 4.6MB |
| 전체 추정 | 남은 고유 약 33,756장, 약 57분, 약 $4 |

### 9.1 OpenRouter 경로 확인 (2026-11-19)

서버 운영 키(`rebom/embed/prod/gemini_api_key`)는 **OpenRouter 키**(`sk-or-v1-…`)입니다. `gemini.py`는 키가 `sk-or-`로 시작하면 OpenRouter `POST /api/v1/embeddings`(모델 `google/gemini-embedding-2`, `dimensions: 768`)로 보내고, 아니면 Google SDK로 직접 보냅니다. 사진은 같은 전처리(JPEG) 결과를 `data:image/jpeg;base64,…` 로 보냅니다.

| 항목 | 값 |
|---|---|
| 사진 12장 (4개 실행에서 골고루, png 원본 1장 포함), 1장씩 | S3 기존 벡터(Google 직접)와 코사인 **1.000000** (12/12), 768차원, 노름 1.0 |
| 같은 12장, 6장씩 묶음 2번 | S3·1장씩 결과와 코사인 1.000000, 순서 일치 12/12 |
| 코드 경로(`GeminiEmbedder`, 시크릿 → OpenRouter) 6장 | 코사인 1.0, 제공자 `Google AI Studio` |
| 글 | 문장 3개 → 벡터 3개, 1개씩과 묶음 코사인 1.0 |
| 비용 | 사진 1장 258토큰 $0.0001161 (6장 $0.000697). 확인 전체 약 $0.004 |
| 지연 | 1장 요청 p50 1.26초, 최대 10.6초(1번) → 검색 API 5초·재요청 2번 규칙이 필요함 |

벡터가 같으므로 기존 S3·RDS 벡터(42,820칸)는 그대로 쓰고 다시 만들지 않습니다(`model` 값도 `gemini-embedding-2` 유지). `embed` 의 `summary.json` 에 `api_usage`(경로·호출 수·OpenRouter 비용 USD)를 남겨 비용을 실측합니다. 키의 지출 상한은 OpenRouter에서 $20 입니다.

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
| 카드 내용 | **A 템플릿**. `src/rebom/embedding/handoff_20261120/text_card_reference.py`의 `build_card()`와 글자까지 같게 만듭니다 (벤치마크 글 960/960 일치) |
| Silver 칼럼 | Silver 이름을 API 이름(kindNm·age·sexCd·neuterYn·weight·colorCd·specialMark)으로 바꿔 넣습니다. A2의 특징 글은 정제 단계가 `specialMark`·`sfeSoci`·`sfeHealth` 등을 합쳐 정리한 문장 칼럼 하나를 씁니다(11-18 결정, 칼럼 이름은 정리 뒤 확정). Silver의 `color_tag`·`size`는 쓰지 않고, 원래 털색·몸무게 값에서 같은 규칙으로 다시 계산합니다 |
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
| 8 | A1: specialMark / A2: 정리된 특징 문장 (specialMark + sfeSoci + sfeHealth 등) | `. 특징: <글>` (관리 번호 지우기·전화번호 가리기는 똑같이) |

**템플릿 버전**
- `A1` = 1~6 + 8 (벤치마크와 같음, 사회성 sfeSoci 없음). **smoke ① 카드 일치 확인에만** 씁니다.
- `A2` = A1 + 7(태그). **첫 적재는 A2로 한 번에** 합니다. 태그는 각 담당자가 로컬에서 성능을 확인하고 Silver 칼럼으로 붙인 뒤 씁니다.
  - 태그 칼럼 이름·값 목록·이름표 말은 담당자와 정합니다 [결정 필요].
  - 사회성(sfeSoci)·건강(sfeHealth)은 특이사항과 합친 정리 문장 하나로 들어갑니다 (11-18 결정). 이 문장은 벤치마크로 재지 않은 형식이라 A1·A2 비교에서 같이 봅니다.
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
- 입양·반환으로 끝난 공고는 벡터를 지우지 않고, 검색할 때 공고 상태(기본 `보호중`)로 거릅니다 [확정, 11/19, 사진과 같음].

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
  1. **카드 일치**: Silver 칼럼으로 만든 A1 카드(사회성 빼고 — `build_card`는 `soci=True`가 아니면 sfeSoci를 넣지 않음)가 기준 `text`와 글자까지 같은가 → 다르면 칼럼 매핑 문제. `handoff_20261120/silver_schema_check.py --card-ref text_smoke_ref.parquet`로 미리 확인 가능
  2. **벡터 일치**: 기준 `doc_input`을 Gemini 768로 임베딩한 값과 기준 `vec`의 코사인이 0.99 이상인가 → 다르면 모델 이름·차원·접두어 문제
  3. 카드 질의로 자기 공고 찾기
  4. 글 질의 지연 p95 (서버 Pod에서. Colab에서는 0.218초)

### 10.5 검색에서 합치는 방법 (모델선정 문서 8장 "검색 순위 규칙")

- 글 질의 → Gemini(질의 접두어) → `text_embedding`. 사진 질의 → Gemini(사진 전처리) → `image_embedding`.
- 두 목록은 **순위**로 합칩니다: `w글/(60 + 글 순위) + w사진/(60 + 사진 순위)`. 사진 순위는 공고별 최고 유사도 순위입니다(4절, 11/19 확정).
- 가중치: 첫 화면 0.6 : 0.4, 더보기 옆 토글로 "특징 중심" 0.7 : 0.3 · "생김새 중심" 0.4 : 0.6. C1 기준으로 잰 값이라 B로 다시 재서 바꿀 수 있습니다 (I3d). 이미 보여 준 공고는 뺍니다.
- 바깥 기준은 필터 단계입니다: 값이 맞음 → 미상 1개 → 미상 2개. 값이 다른 공고는 뺍니다.
- **글 질의로 사진 표도 찾기**: 같은 질의 벡터로 `image_embedding`도 검색해 세 번째 순위 목록으로 합칠 수 있습니다(추가 API 호출 없음). 외모 질의에서 도움이 되는지 I4로 잰 뒤 정합니다 [결정 필요].
- **API 장애**: 글·사진 질의 모두 Gemini를 부르므로, 한 번 부를 때 **5초**를 기다리고 응답이 없으면 **2번 더** 부릅니다(429·5xx·시간 초과만, 짧은 간격). 세 번 모두 실패하면 필터 검색(SQL)만 보여 주고 안내합니다. 최악의 경우 약 15초가 걸리므로, 화면은 기다리는 동안 필터 결과를 먼저 보여 주는 방식을 권합니다 [제안].

### 10.6 용량·비용 예상 (추정)

- 공고 약 18,854건 × 768차원: RDS 테이블 약 130MB, HNSW 약 60MB. 사진(약 130MB)과 합쳐도 `db.t4g.micro` 메모리 안에 들어갈 것으로 봅니다. 실측 뒤 확정합니다.
- API 비용: 3년치 첫 적재 약 $0.4~1.1 (카드당 100~300토큰 가정, 100만 토큰당 $0.20), 매일은 바뀐 공고(하루 약 200건)만 하므로 월 $0.1~0.4.
- 서버 메모리: 임베딩 모델을 올리지 않으므로 추가 없음.
- 템플릿을 바꾸면 전체를 다시 만들어야 하므로 템플릿 변경은 모아서 합니다. 이전 버전 벡터는 S3에 남겨 비교와 되돌리기에 씁니다.

## 11. 남은 일·결정 필요

- **[확정, 11/19]** 종료 공고 벡터는 지우지 않고 검색에서 공고 상태로 거릅니다. 검색 API는 기본으로 `보호중` 공고 안에서만 찾습니다(4절). 공고 상태를 읽을 RDS 서비스 테이블(B)과 조인 방법은 검색 API를 만들 때 정합니다.
- **[남음]** 보호중 공고에서 사진 칸만 빠진 경우(API에서 popfile이 사라짐): Bronze manifest에 "칸 삭제" 기록이 없어 옛 벡터가 남고 `rds_not_current`로만 셉니다. Silver 사진 현재 표가 운영되면 그 기준으로 정리합니다.
- **[결정 필요]** 빈 사진 기준(표준편차 4.0)이 이 데이터에 맞는지. 시범 1,100장에서는 0장이었습니다.
- 인프라 문서의 pgvector 확장 설치 주체 정정 (`infra/terraform/database/README.md`).
- 서버 실행: `infra/docker/embed.Dockerfile`, ECR `rebom/embed`, k8s 네임스페이스·권한, DAG 태스크 (배포는 사람).
- S3 실행 폴더가 늘어나면 현재 상태를 한 파일로 모으는 정리(compaction) 검토. `load`·`embed`는 매번 모든 parquet를 읽습니다.
- 텍스트 임베딩 파이프라인 구현 (10절, Gemini. Silver 태그 칼럼이 완성된 뒤). **[결정 필요]** A2 태그 칼럼·값·이름표, 정리된 특징 문장 칼럼 이름, 성별·중성화·나이를 카드에서 뺄지, 글 HNSW 사용 여부, 글 질의로 사진 표도 찾을지(I4).
