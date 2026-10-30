# Re:봄 Git 브랜치 규칙 · 폴더 구조

> 상태: **1차 확정안 (2026-10-30)** · AI 코딩 도구용 요약은 `AGENTS.md` (이 문서와 같이 고칠 것)
> 방향: 규칙은 **최소한만**, 막히는 상황에서는 **본인 판단으로 진행**할 수 있게

---

## 1. 브랜치 규칙

### 한눈에 보기

```
main      ──●───────────────●──────────────●──   ← 데모/배포 기준 (마일스톤마다 태그)
             \             /              /
develop   ────●──●──●──●──●──●──●──●──●──●────   ← 평소 PR은 전부 여기로
                 \  /  \  /     \   /
feat/…            ●●    ●●       ●●●              ← 개인 작업 브랜치 (짧게 쓰고 삭제)
```

| 브랜치 | 용도 | 들어오는 방법 |
|---|---|---|
| `main` | 발표·데모·배포 기준 | `develop` → `main` PR (마일스톤 때만) |
| `develop` | 평소 작업 통합 | 작업 브랜치 → `develop` PR |
| 작업 브랜치 | 개인 작업 | 본인 마음대로 push, 머지 후 삭제 |

- `main`, `develop` 에는 **직접 push 하지 않고 PR로만** 합칩니다 (기록을 남기기 위함)
- 팀원은 **포크 말고 이 레포에 브랜치를 만들어** 작업합니다. 포크에서 올린 PR은 Discord 알림이 가지 않습니다
- 급한 수정이 필요하면 `fix/…` 브랜치를 `main` 으로 바로 PR → 머지 후 `main` 을 `develop` 에 한 번 합쳐 줍니다

### 1-1. 브랜치 이름

```
<type>/<scope>-<짧은-설명>      예) feat/ingest-incremental, fix/clean-age-parse
```

- 영어 소문자 + 하이픈. 설명은 2~4단어면 충분

| type | 언제 |
|---|---|
| `feat` | 새 기능 |
| `fix` | 버그 수정 |
| `docs` | 문서 |
| `infra` | Docker, k3s, CI 설정 |
| `exp` | 실험·노트북 (머지 안 해도 됨) |

| scope | 역할 | 담당 |
|---|---|---|
| `ingest` | 수집 | 현지 |
| `clean` | 정제·스키마 | 광훈 |
| `embed` | 임베딩 | 동현 |
| `tag` | 태깅·추론 | 권능 |
| `serve` | 검색 API·챗봇 | 민혁 |
| `front` | 프론트엔드 (Next.js) | 미정 |
| `common` | 공통·여러 역할에 걸친 작업 | 누구나 |

> 이름이 규칙과 조금 달라도 문제 삼지 않습니다. 목적은 브랜치 목록만 보고 누가 뭘 하는지 알아보는 것입니다.

### 1-2. 커밋 · PR 제목

`develop` 에는 **Squash merge** 로 합치기 때문에, **PR 제목만** 형식을 맞추면 됩니다.
작업 브랜치 안의 개별 커밋 메시지는 자유입니다.

```
<type>(<scope>): 무엇을 했는지      예) feat(ingest): 수정일 기준 증분 수집 추가
```

- 한국어로 써도 됩니다
- `.env`, 원본 데이터, 모델 가중치는 커밋하지 않기 (이것만은 꼭 지키기)

### 1-3. PR · 리뷰

1. PR 대상은 `develop` (마일스톤 때만 `develop` → `main`)
2. 기본은 **팀원 1명 승인 후 머지**
3. 다음 경우에는 **승인 없이 본인이 머지해도 됩니다** (자가 머지)
   - 하루 정도 기다려도 리뷰가 없을 때
   - 발표·데모 직전처럼 급할 때
   - 문서·오타처럼 작은 변경일 때
   - 그 밖에 본인이 필요하다고 판단할 때
4. 자가 머지할 때는 **PR 본문에 이유 한 줄**만 남겨 주세요. 나중에 팀원이 보고 문제가 있으면 새 PR로 고칩니다
5. DB 스키마나 `docs/data_contract.md` 처럼 다른 사람 작업에 영향을 주는 변경은, 머지 전후로 Discord에 한마디 남겨 주세요
6. 작업 중이면 Draft PR로 먼저 올려도 좋습니다. PR 알림은 Discord 포럼에 자동으로 올라갑니다

> GitHub는 작성자가 자기 PR에 Approve를 누르는 것을 막습니다. 그래서 자가 머지는 아래 1-5의 설정(`develop` 필수 승인 0명, `main` 은 관리자 우회)으로 가능하게 해 둡니다.

### 1-4. 태그 (선택)

마일스톤 때 `develop` → `main` 머지 후 태그를 붙입니다. 예: `v0.1` (수집·정제), `v0.2` (임베딩·태깅·검색), `v0.3` (챗봇), `v1.0` (최종). 주차는 WBS에 맞춰 조정.

### 1-5. GitHub 설정 (레포 관리자 1회)

레포가 public 이라 무료 계정에서도 아래 설정을 모두 쓸 수 있습니다.

- [ ] `develop` 브랜치 만들고 **기본 브랜치로 지정** (PR 대상을 따로 고르지 않아도 되게)
- [ ] Settings → Rules → **Rulesets** 에서 `main`, `develop` 대상으로 규칙 하나 생성
  - *Restrict deletions* ✅ · *Block force pushes* ✅ · *Require a pull request before merging* ✅
  - Required approvals: **0** → 승인 없이도 머지 가능 (승인은 팀 약속으로만)
  - (선택) `main` 만 승인 1명으로 따로 두고 싶다면, `main` 용 규칙을 따로 만들고 *Bypass list* 에 **Repository admin** 추가 → 급할 때 관리자가 승인 없이 머지 가능
- [ ] Settings → General → Pull Requests
  - *Allow squash merging* ✅ (작업 브랜치 → `develop`)
  - *Allow merge commits* ✅ (`develop` → `main` 용, 끄지 않기)
  - *Automatically delete head branches* ✅ (위 *Restrict deletions* 덕분에 `develop` 은 지워지지 않음)
- [ ] 팀원 4명을 Collaborator(Write)로 초대

---

## 2. 폴더 구조

원칙은 **역할 = 폴더** 입니다. 각자 자기 폴더에서 작업하면 충돌이 적고, 누구에게 리뷰를 부탁할지도 바로 보입니다.
지금 다 만들 필요 없이, **필요해질 때 그 폴더를 만들면** 됩니다.

```
re-bom/
├── README.md
├── .env.example               # 키 이름만. 실제 값은 .env (커밋 금지)
├── .gitignore
├── pyproject.toml             # 패키지·의존성·pytest 설정
├── docker-compose.yml         # 로컬 PostgreSQL+pgvector (+ Airflow)
│
├── .gitattributes             # 줄바꿈(LF) 통일. Windows·Mac 섞여도 diff가 깨지지 않게
├── AGENTS.md / CLAUDE.md      # AI 코딩 도구용 규칙 요약
│
├── .github/
│   ├── workflows/
│   │   ├── discord-pr-notify.yml   # PR·리뷰·댓글 → Discord 포럼
│   │   ├── notion-decision-issue.yml  # 노션 '결정' → GitHub 이슈
│   │   └── ci.yml                  # (나중에) 테스트 자동 실행
│   ├── scripts/                    # 워크플로가 쓰는 스크립트
│   └── pull_request_template.md    # PR 본문 양식 (사람·AI 공용)
│
├── dags/                      # Airflow DAG. 로직은 src/rebom 함수를 불러서 사용
│
├── src/rebom/
│   ├── common/                # 공통: 설정(.env), 로깅, DB·S3 연결
│   ├── ingestion/             # 현지  API 수집, 사진 다운로드
│   ├── cleaning/              # 광훈  정제, 나이·체중 파싱, 적재
│   ├── embedding/             # 동현  텍스트·이미지 임베딩
│   ├── tagging/               # 권능  Jev 추론, 속성 태그
│   └── serving/               # 민혁  검색 API, 챗봇
│
├── frontend/                  # Next.js 웹·모바일 화면 (자체 AGENTS.md 있음)
├── db/migrations/             # 스키마 SQL (001_init.sql, 002_… 순서대로 추가)
├── infra/                     # Dockerfile, k3s 매니페스트
├── scripts/                   # 일회성 스크립트 (API 점검, 리포트 등)
├── notebooks/                 # EDA·실험 (커밋 전 출력 지우기 권장)
├── tests/                     # 테스트 + fixtures(샘플 JSON, 개인정보 제거)
├── docs/                      # 데이터 계약(data_contract.md), 아키텍처, 결정 기록
└── data/                      # 로컬 데이터 (git 제외)
    └── sample/                # 예외: 팀 공용 샘플은 커밋 (개인정보 제거 후)
```

### 2-1. 꼭 지킬 것 3가지

1. **비밀값·개인정보는 커밋 금지**: API 키는 `.env` 에만. 보호자 실명(`careOwnerNm`)·연락처는 코드·노트북·샘플 파일에 넣지 않기
2. **스키마는 새 파일로 추가**: 이미 `develop` 에 들어간 마이그레이션 SQL은 고치지 말고 다음 번호 파일을 추가 (다른 사람 DB가 꼬이지 않게)
3. **DAG에는 순서만**: 실제 처리 코드는 `src/rebom/<역할>` 에 두고 DAG는 그 함수를 호출만 (DAG 없이도 테스트 가능하게)

여러 역할이 같이 쓰는 코드는 `common/` 에 두면 됩니다.

### 2-2. `.gitignore` · `.gitattributes`

`.gitignore` 는 Python 기본 템플릿에 아래를 반영했습니다.

```gitignore
# 기본 템플릿의 lib/ 를 루트 전용으로 좁힘 (frontend/src/lib/ 가 무시되던 문제)
/lib/
/lib64/

# 데이터·모델 (팀 공용 샘플 data/sample/ 만 예외로 커밋)
data/*
!data/sample/
models/
*.parquet
*.safetensors
*.pt
*.onnx
logs/
.idea/
.vscode/
```

`.gitattributes` 의 `* text=auto eol=lf` 는 줄바꿈을 LF로 통일합니다. Windows에서 파일을 열고 저장만 해도 PR에 파일 전체가 바뀐 것으로 나오는 문제를 막습니다.

---

## 3. 팀이 정할 것

1. `develop` 브랜치를 둘지, `main` 하나로 갈지 (추천: 둠. 데모용 `main` 을 안정적으로 유지하기 좋음)
2. `main` 에도 승인 1명을 요구할지 (추천: 처음엔 0명, 불편함이 없으면 그대로)
3. 패키지 이름 `rebom` 확정
