# Gold 스키마 구현 기준

물리 테이블과 컬럼은 [데이터 계약서 13절](data_contract.md#13-사진-태그-gold-모델-결과-test-환경-2026-11-20)을 기준으로 합니다.

| 구간 | 결정과 구현 상태 |
| --- | --- |
| 버전 정의 | Gold의 `gold_version_registry`에서 작업별 `task@N` 설정을 등록·조회합니다. 등록 및 해시 검증 코드가 있습니다. |
| 결과와 로그 | 사진 태깅·사진 임베딩과 글 임베딩 후보 실행은 등록 버전을 결과와 처리·요청 로그에 기록합니다. 로그는 태깅·임베딩이 공용 `gold_inference_log`·`gold_model_request_log`를 같이 쓰고 `task`로 구분합니다(11/24 결정). 사진 작업의 기존 실행은 종전 키를 씁니다. 기존 행의 공통 버전 매핑은 후속입니다. |
| Silver 입력 | 기존 적재·복구 저널을 유지합니다. Gold가 함께 읽을 공고·현재 사진 Iceberg 스냅샷은 `silver_publish_log`의 `READY` 발행본으로 전달하는 방향입니다. 발행과 Gold 소비는 구현 전입니다. |
| RDS 서비스 | 일일 배치가 시작 시 읽을 운영 버전과 검색 API가 사용할 버전을 RDS `active_version`으로 관리하는 설계입니다. 수동 Airflow 실행은 지정한 후보 버전을 준비합니다. 활성 설정과 전환은 구현 전입니다. |

## 사진 후보 버전 실행

1. 관리자가 `gold_catalog --create --group registry`와 `--group log`, `--group tagging`, `--group embedding`을 각 환경에 적용합니다. 등록 버전으로 실행하기 전에 nullable `version` 컬럼이 필요합니다. 업데이트는 기존 행을 자동 이관하지 않습니다.
2. 실행할 코드의 설정으로 버전을 등록합니다. 사진 임베딩 설정은 `model_id`, `dim`, `preprocess_version`, `blank_std`입니다. 사진 태깅 설정은 `src/rebom/tagging/version_config.py` 의 `runtime_config(task)` 한 곳에서 만들며, `model_id`, `provider`, `prompt_version`, `prompt_sha256`(시스템·질문 문장·응답 형식), `preprocess_version`, `max_image_bytes`, `media_resolution`, `reasoning_effort`, `temperature`, `postprocess`(2회 일치 규칙, 품종은 강 신호 기준과 이름표·62종 목록 해시 `alias_sha256`)입니다. 등록용 JSON 은 `python -m rebom.tagging.version_config --task photo_trait` 로 출력합니다. 질문지 문장이나 이름표를 고치면 `prompt_version` 을 그대로 두어도 해시가 달라져 같은 버전으로는 실행되지 않습니다. 실행하는 코드와 등록 설정이 일치할 때만 후보를 처리할 수 있습니다. 모델이나 전처리를 바꾸는 후보는 그 설정을 지원하는 코드가 필요합니다.
3. 사진 태깅은 `select --version photo_trait@1`로 대상 파일을 만들고 같은 대상 파일로 `run`을 실행합니다. 선택한 작업만 처리하며, 다른 작업도 필요하면 `--version`을 반복합니다. 사진 임베딩은 `embed --gold-env test --version image_embedding@1`과 기존 실행 옵션을 사용합니다. 후보 parquet과 실행 요약은 기존 배치가 읽는 경로 밖에 저장합니다.

## Jev 글 태깅과 A2 카드 연결

권능 님의 11/23 시험 적재 회신 기준입니다. 보호중 개·고양이 공고 200건을 Gold test에 적재했고, 같은 글은 호출을 재사용해 요청 로그는 159행이었습니다. 회신에서 제시한 writer는 `src/rebom/tagging/run_text_tags.py`이며, 이 브랜치의 작업 범위는 소비 계약 정리입니다.

| 테이블 | Jev 저장 단위와 소비에 필요한 필드 |
| --- | --- |
| `gold_animal_tag` | 공고의 태그 하나. `source=text`(공고 글. 지금 적재 코드는 `jev` 로 쓰며, 바뀔 때까지 둘 다 읽음), `subject_type=animal`, `subject_key=공고번호`. `source` 는 출처(photo 사진 · text 공고 글 · rule 공고 칸)만 적고 모델 이름은 `model`·버전으로 구분합니다(11/24). `task`는 `text_personality` 또는 `text_health`입니다. `tag_code`, `source_hash`, 태그 버전, `searchable`, `displayable`을 조회합니다. 별도 `gold_text_tag`는 현재 연결에 필요하지 않습니다. |
| `gold_inference_log` | 공고·작업·버전·입력별 처리 상태. `OK`, `NO_RESULT`(해당 결과 없음), `NO_TEXT`(호출할 글 없음), `FAILED`(호출·판정 실패)를 구분합니다. |
| `gold_model_request_log` | Jev 모델 호출당 한 행. 응답 원문과 비용·토큰을 보관합니다. 같은 글을 여러 공고에서 재사용한 경우 태그의 `detail.request_id`로 실제 호출과 연결합니다. |

- Jev는 `info_text`를 사용하고 값이 비면 `special_mark`를 사용합니다. 전처리한 글의 해시가 처리 로그의 `input_hash`와 태그의 `source_hash`입니다. Jev 작업 브랜치의 writer는 공고·작업·질문지 버전과 `source_hash`를 함께 비교해 교체하므로 이전 글 해시는 해당 교체 범위에서 제외됩니다. 공용 태그 스키마의 물리 식별자는 별도이며, 이 분리는 Jev 교체 조건에서 보장합니다.
- 최신 글의 해시와 작업별 선택 버전이 모두 일치하는 행을 사용합니다. 시험 적재의 작업 버전은 `text-personality-v1`, `text-health-v1`입니다. 이전 글에 성공한 태그가 남아 있어도 현재 글 소비 대상은 아닙니다.
- 성격 라벨은 확률 0.75 이상인 경우만 태그가 됩니다. 건강 라벨 `health.info` / `health.none`은 공고 글에 건강정보가 있는지를 뜻합니다. `NO_TEXT`는 건강 태그도 없는 별도 상태이며 화면에서의 기본 표시는 미정입니다.
- 검색은 보호중과 `searchable=true`를 함께 확인하고, 상세는 공고 노출 정책과 `displayable=true`를 적용합니다. 상세에는 태그 이름을 전달합니다. `similarity`는 확률×100이고 `detail.probs`는 질문별 확률입니다. `detail.input_text`는 마스킹한 전체 입력이며, 구절 단위 근거는 제공되지 않습니다.
- A2 후보 구현은 사진 태깅과 같은 기준으로 현재 사진 중 원본 키와 SHA-256이 있는 가장 작은 슬롯을 대표 사진으로 고릅니다. `photo_trait`의 외형 태그와 현재 글의 Jev 성격·건강 라벨을 가져옵니다. 카드에 들어가는 태그는 `searchable=true`로 제한하며 확률·검토 값은 포함하지 않습니다. 필요한 태그가 아직 없으면 공고 정보로 카드를 만들고, 태그가 준비되어 본문이 바뀌면 다음 실행에서 새 벡터가 생깁니다.
- `gold_text_embedding`은 완성한 카드의 본문·본문 해시·템플릿 버전·사용한 태그 버전·글 벡터를 공고 단위로 저장합니다. 실행 명령은 `python -m rebom.embedding.cli embed-text --version text_embedding@1 --limit 20`입니다. 기본은 조회·계획이며 `--write`를 붙이면 호출하고 저장합니다.
- 등록 설정은 손으로 쓰지 않고 `python -m rebom.embedding.text_version config --template A2 --tag 작업=버전 …`으로 만들어 `text_version register`로 등록합니다. 템플릿 내용 전체와 지문, 작업별 태그 버전이 들어갑니다(`docs/card_templates.md`). Gold test에 공통 등록 버전이 없는 비전 태그를 읽을 때는 해당 작업의 질문지 버전을 선택합니다. 레지스트리와 임베딩 표의 새 컬럼을 먼저 적용해야 합니다.
- 후보 명령은 현재 Silver test 입력을 사용합니다. `publish_id` 기반 공통 완료본 읽기, 정기 DAG, RDS 적재는 후속 연결입니다.

## Silver 완료본

Gold는 실행 시작에 `publish_id`로 입력 스냅샷을 고정하고, 실행 기록에 그 ID를 남기는 구조로 연결합니다. 사진 Version History는 적재·복구에 사용합니다. 사진 실패·지연 시 이전 사진과 새 공고를 합쳐 발행할지, 부분 적재를 허용할 조건, 적용 순서와 스냅샷 보존 기간은 Silver 발행 계약에서 정합니다. 검증된 공고 상태는 사진 경로와 별도로 RDS에 반영합니다.

## RDS 전환과 검증

1. Gold에서 v2 결과를 생성하고 운영 중인 v1과 함께 보존합니다.
2. RDS에 v2를 준비합니다. 같은 모델의 v1·v2는 기존 사진 벡터 키에서 충돌하므로 새 결과 키에 `version`을 포함하는 방향입니다.
3. 보호중 공고와 현재 사진·카드에 맞는 후보 결과, 질의 설정, 벡터 차원, 실제 검색 쿼리의 실행 계획과 결과 수를 검증합니다. 공유 HNSW에서 버전 필터가 결과 수에 주는 영향을 확인하고 버전별 부분 인덱스와 비교합니다.
4. 검증 후 RDS 활성값을 전환합니다. 검색 API는 한 요청에서 활성 버전의 질의 설정과 벡터를 함께 사용합니다. 이전 설정과 결과가 유효한 동안에는 활성값을 되돌려 롤백할 수 있습니다.

후보 생성이나 적재에 실패하면 기존 활성값을 유지합니다. 사진 A가 B로 바뀌었는데 B 벡터가 준비되지 않았으면 B를 표시하고 해당 사진 칸의 A 벡터를 검색에서 제외합니다. 검색은 보호중 공고만 제공하며, 적격 종료 공고의 상세 30일 노출은 공고 상태 경로에서 관리합니다.
