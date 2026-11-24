# 글 카드 템플릿과 버전 관리

공고 1건을 글 카드로 만드는 형식을 버전별 파일로 두고, 그 **내용과 지문**을 Gold 버전 레지스트리에 남깁니다.
코드: `src/rebom/embedding/card_template.py`(읽기·검사·카드 만들기), `text_version.py`(실행 버전 설정), `card_templates/*.json`.

## 1. 두 가지 버전

| | 템플릿 버전 `A1`, `A2` | 실행 버전 `text_embedding@N` |
|---|---|---|
| 뜻 | 글의 형식 (문구·순서·항목) | 벡터를 만든 조건 전체 |
| 관리 | Git의 `card_templates/<버전>.json` | Gold `gold_version_registry` (등록 뒤 변경 불가) |
| Gold 칸 | `prompt_version` | `version` |

같은 A2라도 태그 버전이 바뀌면 템플릿은 그대로이고 실행 버전만 올립니다.

## 2. 무엇이 어디에 기록되나

| 알고 싶은 것 | 위치 |
|---|---|
| 템플릿 내용 전체 | 레지스트리 `config_json.template` (Git 파일과 같은 내용) |
| 템플릿 지문 | 레지스트리 `config_json.template_sha256`, Git `lock.json` |
| 카드 생성 코드의 지문 | 레지스트리 `config_json.golden_sha256` |
| 벡터 하나의 글·형식·조건 | `gold_text_embedding`의 `input_text`, `prompt_version`, `version` |
| 버전별 이력 | `python -m rebom.embedding.text_version history --env test --profile rebom [--full]` |

표에 새 칸은 없습니다.

## 3. 내용이 바뀌면 어디서 막히나

| 바뀐 것 | 막는 곳 |
|---|---|
| 합쳐진 템플릿 파일(A2.json)을 고침 | 읽을 때 `lock.json`과 비교, 테스트 |
| `lock.json`까지 함께 고침 | 테스트(A1·A2 지문 고정), 실행 전 레지스트리 비교 |
| 체중·털색·가리기·태그 이름표 **코드**를 고침 | `golden_sha256` (고정 예시로 만든 카드가 달라짐) |
| 이름은 A2인데 내용이 다른 등록이 있음 | 등록과 실행을 거부 |
| 지문이 없는 옛 등록 (`text_embedding@1`·`@2`) | 계획만 볼 수 있고 쓰기는 거부 |

모두 **모델을 부르기 전에** 멈춥니다. 코드를 일부러 고쳐야 했다면 새 `text_embedding@N`을 등록합니다.
글이 달라지지 않는 정리(리팩터링)는 지문이 그대로라 막히지 않습니다.

## 4. 무엇을 바꾸면 무엇을 올리나

| 바꾸는 것 | 템플릿 파일 | 실행 버전 | 코드 |
|---|---|---|---|
| 문구·순서·항목 빼기 | 새 파일 | 올림 | 없음 |
| 새 필드 | 새 파일 | 올림 | `VALUES`에 값 함수 추가, 담당자 협의 |
| 계산 규칙·이름표 | 그대로 | 올림 | 수정 |
| 태그 버전 | 그대로 | 올림 | 없음 |
| 모델·차원 | 그대로 | 올림 | 설정 |
| 공고 글이나 태그 값 | 그대로 | 그대로 | 없음 (그 공고만 다시 임베딩) |

태그 종류(사진 특징·성격·건강정보)는 아직 코드(`text_batch.TAG_SOURCES`)에 있습니다. 품종 태그처럼 새 종류를 넣는 템플릿을 만들 때 파일로 옮깁니다.

## 5. 새 템플릿 추가 (예: A3)

1. `card_templates/A3.json`을 만듭니다. 기존 파일은 고치지 않습니다.
2. `python -m rebom.embedding.card_template`로 지문을 보고 `lock.json`에 더합니다.
3. 새 값이 필요하면 `card_template.VALUES`에 함수를 더하고 `golden_A3.json` 예시를 둡니다(`golden.json`은 고치지 않음).
4. 테스트와 PR.
5. `_test`에 등록합니다(사람이 실행).
   ```
   python -m rebom.embedding.text_version config --template A3 --tag photo_trait=photo_trait@1 --tag text_personality=text-personality-v1 --tag text_health=text-health-v1 --out text_embedding@4.json
   python -m rebom.embedding.text_version register --env test --version text_embedding@4 --config text_embedding@4.json --profile rebom
   ```
6. `embed-text --version text_embedding@4 --show 5`로 카드를 확인하고(호출 없음) `--write`로 만듭니다. 이전 버전의 벡터는 그대로 남습니다.
7. 검색 품질을 비교한 뒤 서비스 버전을 바꿉니다.

### 파일 형식

| 키 | 뜻 |
|---|---|
| `fields[].value` | 값 함수 이름. 없는 이름이면 멈춤 |
| `fields[].text` | 문구. `{}` 하나에 값이 들어감 |
| `fields[].map` | 코드값 → 글. 표에 없으면 그 항목을 뺌 |
| `separator`, `tail` | 항목 사이 글자, 마지막에 붙는 글 |
| `params` | 크기 경계(10kg 포함 여부), 특징 글(`info_text`·`special_mark`), 사회성 허용 |

전화번호·관리번호 가리기는 값 함수 안에서 하며 템플릿에서 끌 수 없습니다.

## 6. 남은 것
- RDS `002_text_embedding.sql`의 `CHECK (template_version IN ('A1','A2'))`: A3를 RDS에 올리기 전에 새 마이그레이션으로 제거합니다. 어떤 템플릿을 쓸 수 있는지는 레지스트리가 판단합니다.
- 태그 종류를 템플릿 파일로 옮기기, 두 템플릿의 카드를 비교하는 명령.
