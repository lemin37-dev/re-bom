# 검색 API + 화면 테스트 방법

검색 API(FastAPI, sample 모드)와 프론트(Next.js)를 로컬에서 확인하는 순서입니다. 명령은 레포 루트 기준이고, Windows PowerShell 예시를 먼저 적었습니다.

## 1. 자동 테스트

```powershell
# API (처음 한 번: 가상환경·패키지 설치. run_api.ps1 을 한 번 실행했다면 이미 됨)
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[serve,dev]"
.venv\Scripts\python -m pytest              # tests/test_serving_*.py

# 프론트
cd frontend
pnpm install
pnpm lint
pnpm typecheck
pnpm build
```

`pytest` 는 외부 API를 부르지 않고 `data/sample/rescue_sample.json` 만 씁니다.

## 2. 띄워서 보기

터미널 두 개를 씁니다.

```powershell
# 터미널 1: 검색 API → http://127.0.0.1:8000/docs 에서 직접 호출해 볼 수 있음
.\scripts\serve\run_api.ps1

# 터미널 2: 화면 → http://localhost:3000
cd frontend
pnpm dev
```

API 주소를 바꿨다면 `frontend/.env.local` 에 `NEXT_PUBLIC_API_BASE_URL=http://…` 를 적고 `pnpm dev` 를 다시 켭니다 (`frontend/.env.example` 참고).

## 3. 화면별 확인 목록

상단 토글로 **웹 화면 / 앱 화면**, 헤더의 **리멍이 / 봄냥이** 를 바꿔 가며 각각 봅니다. 마스코트 상태 화면은 테마에 따라 포즈가 달라집니다.

| 확인할 것 | 하는 방법 | 기대 결과 |
|---|---|---|
| 첫 목록 | 첫 화면 | 보호 중 100마리 중 8마리, "더 만나기"로 8마리씩 추가 |
| 공고 사진 | 카드·상세 갤러리 | 사진이 뜸. 못 불러오는 사진(2021-11 이전 등)은 일러스트로 바뀜 |
| 조건 검색 | 조건 칩 → 종류·나이·크기·지역 선택 후 검색 | 결과 수가 바뀌고, 카드 나이·체중이 조건 안에 있음 |
| 문장 검색 | "온순", "산책" 입력 후 찾아보기 | 특징 문장에 그 단어가 있는 공고만 나옴 |
| 정렬 | "공고 종료 임박 순" | D-숫자가 작은 공고가 먼저 |
| 상세 | 카드 클릭 | 주소가 `/animals/구조번호`, 공고 기간·타임라인·보호소 전화가 실제 값 |
| 보호센터 | 보호센터 메뉴 → "이 센터 아이들 보기" | 그 센터 공고만 보이고 배너에 센터 이름 |

### 상태 화면 (마스코트)

| 상태 | 만드는 방법 | 화면 |
|---|---|---|
| 로딩 | 브라우저 개발자 도구 → Network → Throttling 을 "Slow 3G" 로 두고 검색 | "다시, 봄이 올 수 있도록" + 뛰는 마스코트 + 노란 진행 막대 |
| 검색 결과 없음 | 검색어에 `없는단어` 입력 후 찾아보기 | "일치하는 동물을 찾지 못했어요." + 조건 넓혀서 다시 찾기 버튼 |
| 빈 목록 | 빈 샘플로 API 실행 (아래) 후 첫 화면 | "아직 등록된 동물이 없어요." |
| 오류 | API 터미널을 Ctrl+C 로 끄고 검색 또는 새로고침 | "친구들 소식을 불러오지 못했어요." + 서버 실행 안내 + 다시 불러오기 |
| 없는 공고 | 주소창에 `http://localhost:3000/animals/000` | "찾는 친구가 여기 없어요." |
| 없는 화면 | `http://localhost:3000/abc` | 같은 안내 + 입양 찾기로 돌아가기 |

빈 샘플로 API 실행:

```powershell
Set-Content -Encoding utf8 empty.json '[]'
$env:REBOM_SAMPLE_PATH = "$PWD\empty.json"
.\scripts\serve\run_api.ps1
# 끝나면: Remove-Item Env:REBOM_SAMPLE_PATH; Remove-Item empty.json
```

macOS·Linux·Git Bash 는 `echo '[]' > empty.json && REBOM_SAMPLE_PATH=$PWD/empty.json scripts/serve/run_api.sh` 입니다.

## 참고

- 검색은 보호 중(`state=protect`) 공고만 보여 줍니다. 종료된 공고는 주소로 직접 열면 볼 수 있습니다.
- 매칭 점수·추천 이유·거리는 아직 데이터가 없어 화면에 나오지 않는 것이 정상입니다.
- 사진 주소가 `http://` 라서, 화면을 https 로 배포하면 이미지 프록시가 필요합니다.
