# AGENTS.md — Re:봄 레포 작업 규칙 (AI 코딩 도구용)

유기동물 입양 멀티모달 검색 파이프라인. Airflow 수집 → 정제 → 임베딩·태깅 → pgvector 검색 API. Python, k3s 배포.
사람용 상세 설명은 `BRANCH_AND_STRUCTURE_DRAFT.md`. 둘이 다르면 이 파일을 따르고, 차이를 사용자에게 알린다.

## Git
- `main`, `develop` 에 직접 commit·push 하지 않는다. force push, 브랜치 삭제, 히스토리 재작성 금지.
- 사용자가 요청하지 않으면 commit·push·PR 생성 같은 git 작업을 하지 않는다. 변경은 파일 수정까지만 하고 알려준다.
- 브랜치 이름: `<type>/<scope>-<짧은-설명>` (영어 소문자, 하이픈). 예: `feat/ingest-incremental`
  - type: `feat` `fix` `docs` `infra` `exp`
  - scope: `ingest` `clean` `embed` `tag` `serve` `front` `common`
- PR 대상은 `develop`. PR 제목: `<type>(<scope>): 무엇을 했는지` (한국어 가능). 예: `feat(ingest): 수정일 기준 증분 수집 추가`
- 개별 커밋 메시지 형식은 자유지만, 위 PR 제목 형식을 쓰면 좋다.
- PR을 만들 때는 `.github/pull_request_template.md` 의 항목을 모두 채운다. '확인한 것'에는 실제로 실행한 명령과 결과만 적고, 실행하지 못한 것은 그렇다고 적는다. '작성 정보'에 AI 도구 이름과 요청자를 적는다.
- PR은 Draft로 올린다. 리뷰어 지정·머지는 사람이 한다.

## 코드를 어디에 두나
| 내용 | 위치 |
|---|---|
| API 수집, 사진 다운로드 | `src/rebom/ingestion/` |
| 정제, 나이·체중 파싱, 적재 | `src/rebom/cleaning/` |
| 텍스트·이미지 임베딩 | `src/rebom/embedding/` |
| 태깅·추론 | `src/rebom/tagging/` |
| 검색 API, 챗봇 | `src/rebom/serving/` |
| 웹·모바일 화면 (Next.js) | `frontend/` (그 안의 `AGENTS.md` 도 따른다) |
| 설정·로깅·DB/S3 연결 등 공용 코드 | `src/rebom/common/` |
| Airflow DAG | `dags/` |
| DB 스키마 SQL | `db/migrations/` (`001_init.sql`, `002_…`) |
| Dockerfile, k3s 매니페스트 | `infra/` |
| 일회성 스크립트 | `scripts/` |
| 실험·EDA | `notebooks/` |
| 테스트, 테스트용 작은 JSON | `tests/`, `tests/fixtures/` |
| 팀 공용 API 샘플 데이터 | `data/sample/` (개인정보 제거 후) |
| 워크플로·워크플로용 스크립트 | `.github/workflows/`, `.github/scripts/` |
| 문서 (데이터 계약 등) | `docs/` |

- 필요한 폴더가 없으면 위 표대로 만든다. 표에 없는 최상위 폴더는 새로 만들지 않는다.
- `lib` 라는 폴더 이름은 쓰지 않는다 (`.gitignore` 에 걸림).
- 맡은 작업의 scope 폴더 밖 코드를 고쳐야 하면, 고치기 전에 사용자에게 알린다.

## 반드시 지킬 것
1. **비밀값·개인정보 금지**: API 키·토큰은 `.env` 에서만 읽는다. 코드·노트북·테스트 데이터에 키를 쓰지 않는다. `.env` 는 읽기만 하고 수정하지 않는다 (새 키가 필요하면 `.env.example` 에 이름만 추가).
   보호자 실명(`careOwnerNm`)·연락처는 저장·출력·샘플에 넣지 않는다.
2. **마이그레이션은 새 파일로**: 이미 있는 `db/migrations/*.sql` 은 수정하지 않고 다음 번호 파일을 추가한다. 스키마를 바꾸면 `docs/data_contract.md` 도 함께 고친다.
3. **DAG는 호출만**: `dags/` 에는 태스크 순서와 호출만 둔다. 실제 처리 로직은 `src/rebom/<역할>/` 함수로 만든다.
4. **커밋하면 안 되는 파일**: `data/sample/` 을 제외한 `data/` 아래 파일, 모델 가중치(`*.pt`, `*.safetensors`, `*.onnx`), `*.parquet`, 로그.

## 작업 습관
- 코드를 바꾸면 관련 테스트를 `tests/` 에 추가하거나 고치고 `pytest` 를 실행한다.
- 외부 API 테스트는 실제 호출 대신 `tests/fixtures/` 나 `data/sample/` 의 샘플 JSON을 사용한다.
- 새 파일은 LF 줄바꿈, UTF-8 로 저장한다.
- 공공데이터 API의 빈 필드는 키 자체가 빠져 오므로 `dict.get` 으로 다룬다. 기본 키는 `desertionNo`.
