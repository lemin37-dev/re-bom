# 데이터 계약 v0: 구조동물 공고 (A 수집 → B 적재)

> 원본은 노션 "데이터 계약 v0 초안"이고 이 파일은 사본입니다. 바꿀 때는 노션을 먼저 고치고 이 파일을 맞춥니다.

작성: 차현지 · 2026-10-30 · 최종 수정: 2026-11-02(보호중 목록 범위, updTm 문장, 수정일 조회 범위 정정, PR #4 리뷰 반영, 마지막 성공일 겹침·partitions 필드 제안) · 상태: **초안** (이광훈 님과 합의 전)

## 0. 이 문서는 무엇인가

수집(A)이 적재(B)에게 **무엇을, 어떤 형태로 넘기는지**와 각자의 책임 범위를 정한 약속입니다. 구조동물 API만 다룹니다(분실동물은 확장 범위).

표기 규칙:

- **[확정]** 직접 호출해 확인한 사실
- **[제안]** A의 제안. B와 합의가 필요함
- **[결정 필요]** 아직 정해지지 않음. 괄호에 결정할 사람

근거 자료: 노션 "구조 API 검증 결과", "구조 API 요청 조건·응답 필드 정리"

## 1. 책임 경계

| A 수집 (차현지, 서브 서동현) | B 적재 (이광훈, 서브 최권능) |
| --- | --- |
| API 호출, 페이지 순회, 재시도, 중복 수집 방지 | 정제·표기 통일 (품종, 색상, 체중, 나이) |
| 원본 응답 보존 (S3 bronze) | 상태 이력 관리 |
| 사진 URL 수집, 사진 확보(서동현) | silver 테이블, 서비스 테이블 |
| 수집 기록·실패 기록, 원본 인계 | 품질 검사, 미달 시 반영 중단 |

합의 없이 A의 범위를 넓히지 않습니다. A의 중복 수집 방지와 B의 상태 정합성을 섞지 않습니다.

## 2. 수집 방식 (A가 매일 하는 일)

매일 03:00(Airflow, W3 구현)에 세 가지를 조회합니다.

| 단계 | 조회 | 잡는 것 |
| --- | --- | --- |
| ① 수정일 증분 | `bgupd`=마지막 성공일(하루 겹쳐 받음), `enupd`=오늘, 발생일 범위 `bgnde`/`endde`를 반드시 함께 보냄(3년 이하 구간으로 나눠서) | 새 공고, 처음부터 종료로 올라온 공고, 상태 변경 일부 |
| ② 보호중 목록 | `state=notice` + `state=protect` (발생일 제한 없이 전부, 3년 이하 구간으로 나눠서, 아래 참고) | 상태 변경, 사진 칸 추가, 종료에서 보호중으로 복귀 |
| ③ 사라진 공고 조회 (필요할 때만) | DB에는 보호중인데 ②에 없는 공고를 `종료(사유 미상)`으로 표시한다. 종료 사유가 필요해지면 `desertion_no`(유기번호)로 한 건씩, 또는 발생일 단위로 조건 없이 조회한다 | 필요할 때만 종료 사유와 최종 값 |

- **[확정]** 10/30 시험(3일 치 변경): 이 세 단계를 합친 결과가 전부 다시 받은 결과(30,210건)와 공고번호·상태 100% 일치. 내용 차이 40건은 모두 종료 공고의 비고·사진 칸.
- **[확정]** 일 호출 한도 10,000건(응답 헤더 확인). 시험은 3일 치 변경에 80번. **[확정, 11/2]** 발생일 범위(`bgnde`/`endde`)를 안 보내면 API는 최근 약 1개월 발생분만 돌려주고(수정일 조회도 마찬가지), 범위가 약 3년(1,096일)을 넘으면 오류 없이 0건을 돌려줍니다. A는 항상 범위를 보내고, 구간 길이를 1,095일 이하로 제한합니다.
- **[확정]** `state`는 명세에 `notice`(공고중)와 `protect`(보호중)만 있고, `finish`는 호출은 되지만 명세에 없는 비공식 동작입니다. 의존하지 않는 쪽을 권합니다. 공고 한 건을 찾는 조건은 명세의 `desertion_no`, `notice_no`이고 10/30 호출로 확인했습니다(응답 필드 이름으로 보내면 무시됨). **[확정, 11/2]** `desertion_no` 개별 조회는 종료 공고도 돌려주지만 발생일 범위(`bgnde`/`endde`)를 함께 보내야 합니다(없으면 최근 1개월 밖의 공고는 오류 없이 0건). 없는 번호는 결과 코드 00에 0건입니다.
- **[제안]** ②의 발생일 범위(11/2 수정): 보호중 목록은 발생일 제한 없이 전부 받되, 3년 이하 구간으로 나눠서 받습니다. 7/1 이전 발생 보호중은 약 9,740건(2009년 이후)이고, 전체를 받아도 약 40번 호출입니다(7구간, 11/2 시험). 서비스에 노출할 범위는 별개로 팀이 정합니다. (A, B)
- **[확정]** ③의 종료 사유는 받지 않고 "종료(사유 미상)"으로 표시합니다(④ + 필요할 때 ①, 10/31 결정). 검토한 방법: ①발생일 조회 ①′`desertion_no`로 한 건씩 조회 ②공고 기간 뒤 `state=finish`(명세에 없는 비공식 동작) ③전체 재조회 ④받지 않고 "종료(사유 미상)" — ②는 10/31 확인 결과 공고 기간이 끝난 뒤에야 종료 공고가 나와서 채택하지 않음

## 3. 고유키와 변경 판정

- **고유키 [확정]**: `desertionNo` (수집본 29,463건에서 중복 없음). `noticeNo`는 보조 식별용 문자열.
- **변경 판정 [제안]**: `updTm`만으로는 부족합니다. 사진 칸이 나중에 채워져도 `updTm`이 안 바뀐 공고가 90건 있었고, 수정일 조회가 상태 변경 274건을 돌려주지 않았습니다(원인: 발생일 범위를 안 보내서 최근 1개월만 나온 것, 11/2 확인). 그래서 **레코드 내용으로 변경을 판정**합니다. 비교에서 `updTm`은 제외하고, `updTm`은 따로 보존합니다. 판정 방식(내용 해시, 컬럼 직접 비교, 핵심 필드 비교)은 **B가 구현 도구에 맞춰 정합니다.** A는 받은 값을 가공 없이 넘기고 변경 비교는 하지 않습니다. 10/30 시험은 레코드 전체를 필드별로 비교해서 통과했습니다. `updTm`은 변경이나 방치 여부의 판단 근거가 아니라 참고 정보입니다. (11/2 추가)
- **임베딩 갱신 대상 [제안]**: 사진 칸(`popfile1`~`popfile8`)이나 특징 문장(`specialMark`)이 바뀐 공고. 사진 칸은 `updTm`이 안 바뀌므로 위 내용 판정으로만 알 수 있습니다. **[결정 필요]** 갱신 대상 필드 목록 (서동현, B)
- **상태 이력 [제안]**: API는 마지막 값만 줍니다(한 공고는 하루에 한 번만 나옴). 중간 상태는 우리가 **매일 받은 값을 쌓아서** 만들어야 하며, 이력 테이블은 B가 만듭니다. A는 수집 실행별 원본 보존으로 뒷받침합니다.
- **복귀 [확정]**: 종료에서 보호중으로 돌아온 공고가 4건 있었습니다. B는 이력에서 "보호중 복귀"를 허용해야 합니다.
- **사라진 공고 [제안]**: Silver 단계의 이벤트 생성(구현 A, 아래 "사라짐 이벤트 단계")이 "보호중 목록에서 사라짐" 이벤트를 만듭니다. 수집(Bronze)은 원본 저장과 구간별 건수 기록까지만 합니다. 최종 값은 필요할 때만 조회해서 넘깁니다. 검색에서 내리는 것은 B의 서비스 테이블 규칙입니다. 사라졌다가 목록에 다시 나타나면 보호중으로 되돌립니다. **종료 판정 조건 [제안]**: 보호중 목록(`notice`, `protect`)의 모든 구간 조회가 아래를 만족할 때만 "목록에서 사라짐"을 종료로 판정합니다. ① 호출이 성공하고 결과 코드가 00입니다. ② 구간 길이가 1,095일 이하입니다(넘으면 요청 전에 막음). ③ 받은 건수가 `totalCount`와 같습니다. `totalCount`가 0이어도 위 조건을 지켰다면 정상입니다(공고 기간 중인 옛 공고가 없으면 `notice` 구간은 0건이 맞음). 받은 건수가 `totalCount`보다 적은 구간이 있으면 그 구간의 부재는 종료로 바꾸지 않고, `desertion_no`로 한 건씩 조회해 확인한 뒤에 판정합니다(11/2 시험: `protect` 2023~2025 구간에서 총건수 4,796, 받은 건수 4,793). 모든 구간 결과는 manifest `partitions`에 구간, 총건수, 받은 건수와 함께 기록합니다. `desertion_no` 개별 조회까지 실패하거나 결과가 불확실하면 기존 상태를 유지하고 재처리 대상(`reprocess_ids`)으로 남깁니다(B 요청, 11/2 합의. 11/3 이후 위치는 아래 이벤트 단계의 manifest). 이벤트 생성은 구간 판정이나 개별 조회로 확정한 공고만 사라짐 이벤트로 넘기고(`confirmed_by`=`complete_range` 또는 `lookup`), 확정하지 못한 공고는 이벤트 없이 `reprocess_ids`로 남깁니다. B는 이 이벤트를 확인된 공고(`confirmed_missing_ids`)로 받고, 확인되지 않은 공고는 기존 보호중 상태를 유지하며 `reprocess_ids`를 참고합니다 **[확정, 11/2]**(이광훈 동의).

- **사라짐 이벤트 단계 [제안, 11/3]**: 사라짐 판단과 이벤트 생성은 API 응답이 아니라 해석이라서 Silver 단계로 분리합니다(구현 A, `rebom.storage.silver.generate_ingest_events`). 수집(Bronze)은 `updated`, `list_notice`, `list_protect`와 manifest(`partitions`)만 남깁니다.
  - **입력/출력**: 입력은 같은 `dt`/`run_id`의 Bronze 파일(체크섬 확인). 출력은 `silver/rescue/events/dt=YYYY-MM-DD/run_id=<같은 id>/`의 `events.jsonl.gz`, `lookup.jsonl.gz`(번호로 조회했을 때만, 조회 응답 사본), `manifest.json`. 올리는 순서는 events, lookup, manifest(마지막, 덮어쓰기 금지), 상태 파일입니다. 이벤트 단계의 상태는 `state/rescue_events_state.json`(보호중·종료 목록, 마지막으로 처리한 `run_id`)이고, 없으면 11/3까지 수집 상태 파일에 있던 목록을 이어받습니다. 실행 순서는 수집, 이벤트 생성, B의 이력 변환입니다.
  - **vanished**: `event`, `desertionNo`, `happenDt`, `detected_at`, `confirmed_by`(`complete_range` 또는 `lookup`), `state_source`, `processState`.
  - **state_source** (`processState` 값의 출처): `updated`(같은 실행 수정분의 종료 상태), `lookup`(구간이 불완전해 번호로 조회해 종료를 확인), `relookup`(구간은 완전하지만 수정분에 종료 근거가 없어 번호로 조회해 종료를 확인), `none`(번호 조회가 0건이거나 알 수 없는 상태라 `종료(사유 미상)`으로 적음, 추정).
  - **returned**: `event`, `desertionNo`, `happenDt`, `detected_at`.
  - **확정 전 재조회**: 구간이 완전해도 수정분에 종료 상태가 없는 사라짐은 번호로 한 번 조회한 뒤에 확정합니다. 조회에서 아직 보호중·공고중이면 확정하지 않고 보호중으로 둡니다(충돌). 호출이 실패했거나 조회 상한(기본 300건)을 넘어 확인하지 못하면 확정을 미룹니다(`lookup_deferred_ids`). 11/3 실행에서 사라짐 7건이 수정분에 없었는데 번호로 조회하니 6건은 종료가 아닌 보호중이었습니다(해당 접수일 목록이 일시적으로 그 6건을 뺐고 총건수도 같이 줄어 구간은 "완전"으로 판정됨, 지금은 목록에 다시 나와 있음).
  - **이벤트 단계의 manifest**: `bronze_run_id`, `generated_at`, `files`(checksum 포함), `vanished_detected`, `confirmed_missing`, `confirmed_by`(`complete_range`·`lookup` 건수), `still_protected`, `reprocess_ids`, `lookup_deferred_ids`, `returned`, `conflicting`, `conflicts`(충돌 번호별 `desertionNo`, `happenDt`, `list_state`, `lookup_state`), `lookup_count`. 충돌은 이벤트가 아니라 여기에만 남깁니다(B의 이벤트 분류가 `vanished`/`returned` 밖의 종류를 오류로 보기 때문).
  - **B가 상태를 읽는 순서 [제안]**: `events.processState`를 무조건 우선하지 않고 근거 기준으로 판단합니다. `lookup`·`relookup`(조회로 확인한 값) > `updated`(수정분의 종료 상태) > `none`(종료는 맞지만 사유는 모름). 조회 원본은 같은 실행의 이벤트 폴더 `lookup.jsonl.gz`에서 `desertionNo`로 찾습니다. `state_source`가 없는 11/3 이전 실행의 이벤트(Bronze 경로의 `events.jsonl.gz`)는 `confirmed_by`만 보고 판단합니다.
  - **[결정 필요] B 쪽 수정**: 이벤트가 Bronze 실행 폴더에서 Silver 경로로 옮겨 가므로, `load_bronze.py`의 필수 파일(`REQUIRED_KINDS`)에서 `events`를 빼고 이벤트를 따로 읽는 연결이 필요합니다. 수정분이 옛 상태일 때(11/3의 `447509202600199`) `lookup` 결과를 현재 스냅샷에 반영해야 `current_state_conflict`가 나지 않습니다. 재처리 대상은 Bronze manifest가 아니라 이벤트 단계 manifest의 `reprocess_ids`/`lookup_deferred_ids`입니다. (이광훈)

## 4. 날짜·시각 필드의 의미

| 필드 | 뜻 | 형식 | 비고 |
| --- | --- | --- | --- |
| `happenDt` | 접수일(명세 이름, 우리 문서의 발생일) | `20260909` | **[확정]** `bgnde`/`endde`는 이 값 기준. 등록일이 아님 |
| `noticeSdt` / `noticeEdt` | 공고 시작일 / 종료일 | `20260907` | **[확정]** 대부분 10–13일. 입양·기증·방사는 공고 기간이 끝난 뒤에만 나옴 |
| `updTm` | 마지막 수정 시각 | `2026-10-28 15:54:54.0` | **[확정]** `bgupd`/`enupd` 기준. 시간대는 미확인 |
| `fetched_at` | 우리가 받은 시각 | ISO 8601, 한국 시간, 초 단위 (`2026-11-03T03:00:12+09:00`) | **[제안]** A가 실행 시작 시각을 붙임. 한 실행의 모든 파일이 같은 값을 씀 |

- 세 가지를 구분해서 컬럼으로 둡니다: 공고 시각(`happenDt`, `noticeSdt`, `noticeEdt`), 수정 시각(`updTm`), 수집 시각(`fetched_at`).
- 형식 통일(문자열 `YYYYMMDD`와 `2026-10-24 11:00:00.0`을 날짜·타임스탬프 타입으로)은 B의 정제 규칙입니다. **[결정 필요]** 시간대 (B)

## 5. 원본 보존 (S3 bronze)

- **경로 [제안]**: `bronze/rescue/raw/dt=YYYY-MM-DD/run_id=<id>/<종류>.jsonl.gz`, 종류는 `updated`(수정일), `list_notice`, `list_protect`. 사라짐·복귀 이벤트와 번호 조회 응답(`lookup`)은 API 응답이 아니라 해석 결과라서 Silver 경로(`silver/rescue/events/...`, 3절 사라짐 이벤트 단계)에 둡니다.
- **`dt`는 수집을 실행한 날**입니다(공고 날짜가 아님). 3년치 백필은 `dt=백필 실행일`에 쌓고, 받은 기간은 manifest의 `request_params`에 남깁니다.
- **파일 형식 [제안]**: 실행당 종류별로 **파일 하나**(JSONL, gzip)로 합쳐 작은 파일이 많이 생기지 않게 합니다. 한 줄이 응답의 공고 한 건이고 가공하지 않습니다. 요청별 `totalCount`와 페이지 수는 manifest에 남깁니다.
- 요청 파라미터와 응답의 공고 항목을 가공 없이 저장하고 **인증키는 제외**합니다.
- **재실행 [제안]**: 실행(run)마다 새 경로에 쌓고 덮어쓰지 않습니다. "같은 날 재실행해도 건수 동일"은 B가 `desertionNo` 기준으로 MERGE해서 보장하고, 내용이 같으면 변경 없음으로 처리합니다.
- **`run_id` [제안]**: `fetched_at`의 한국 시간 날짜·시각에서 기호를 뺀 값에 무작위 6자리를 붙인 것입니다(`2026-11-03T03:00:12+09:00` → `20261103T030012-e9a4ff`, 이광훈 동의). 한 실행의 모든 파일이 같은 `run_id`를 쓰고, 날짜·시각 부분이 `fetched_at`에서 만들어져 둘이 어긋나지 않습니다. 같은 날 다시 실행하면 값이 달라지고, `dt`도 같은 한국 날짜입니다.
- **실행 메타 파일(manifest) [제안]**: `source`, `endpoint`, `request_params`(키 제외), `fetched_at`, `run_id`, `record_count`, `page_count`, `dropped_count`(버린 빈 객체 수), `checksum`(SHA-256), `code_version`, `partitions`(구간별 결과). `partitions`의 한 줄은 `kind`, `result_code`(문자열 `"00"`), `start_date`, `end_date`(`YYYY-MM-DD`), `total_count`, `received_count`, `dropped_count`, `duplicate_count`, `complete`, `reason`, `reprocess_ids`입니다. 앞의 다섯 개는 B의 `validate_partition` 인자와 같은 이름이라 그대로 넘길 수 있습니다. **[확정, 11/2]** 이광훈 동의: 현재 필드 이름과 `YYYY-MM-DD` 형식을 유지합니다. 받은 건수가 `totalCount`보다 적은 구간은 반으로 쪼개 어긋나는 날까지 좁혀 기록하므로 `partitions`의 길이는 실행마다 다르고, 날 단위 구간은 `start_date`와 `end_date`가 같습니다(11/2 시험: 불완전한 날은 2023-02-11, 2024-05-09, 2025-09-09). 사라짐 요약 필드(`vanished_detected`, `confirmed_missing`, `confirmed_by` 등)는 수집 manifest에 두지 않고 3절 이벤트 단계의 manifest에 둡니다.
- **마지막 성공 기록(워터마크) [확정, 11/2]**: A가 마지막으로 성공한 `enupd`를 기록하고, 다음 실행은 그 날짜부터(하루 겹쳐) 오늘까지 한 번에 받아 따라잡습니다. 03:00 실행이면 마지막 성공일 낮에 바뀐 공고가 "다음 날부터"로는 빠지기 때문이며, 겹친 하루는 B가 `desertion_no` 기준 UPSERT와 내용 비교로 거릅니다(이광훈 동의).
- **[확정, 11/2]** S3 업로드 책임: A가 원본 JSONL.gz와 manifest를 올리고, B가 읽어 Silver 변환·적재합니다(이광훈 동의, PR #4 리뷰). 사진은 서동현.

## 6. 사진

- **[확정]** `popfile1`~`popfile8`. 3번 이후는 값이 있을 때만 응답에 나옴. 공고당 평균 2.29장(2장이 87%, 최대 8장). 평균 용량 약 260–310KB, 해상도 중앙값 약 1243×1200px, JPEG가 대부분이고 PNG 약 3%.
- **[확정]** 주소는 전부 `http://`. 오래된 공고의 사진은 404 (2021-11 이전 전부, 2021-12 이후 대부분 열림). 그래서 **공고가 올라온 직후** S3에 저장합니다.
- **경로 [제안]**: `bronze/rescue/images/<desertionNo>/popfile<N>_<sha256 앞 8자>.<ext>`
- **구현 현황 [제안, 11/5]**: PR #16(Draft)에서 아래처럼 구현했고, 위 제안과 다릅니다(해시를 파일 이름에 넣지 않고 manifest에 기록). 원본 `bronze/rescue/images/<desertionNo>/popfile<N>.<확장자>`(받은 그대로, 확장자는 파일 내용으로 판단), 서비스용 `images/<desertionNo>/<N>.webp`와 가로 400px 썸네일 `<N>_thumb.webp`(서비스 이미지 서빙 설계, 11/3), 실행별 manifest `bronze/rescue/image_runs/dt=<실행일>/run_id=<사진 실행 id>/manifest.json`(항목: `desertionNo`, `slot`, `original`, `ext`, `bytes`, `sha256`, `serving.image`, `serving.thumb`). 이광훈·서동현·이민혁 님 확인 뒤 이 절의 경로 제안을 정리합니다.
- **manifest 규칙 [11/5 이광훈 님 동의, `status` 처리·정체 기준은 11/11 합의]** (PR #16 의견 기준):
  - 항목은 `desertionNo`, `slot`, `original`, `ext`, `bytes`, `sha256`, `serving`입니다. `desertion_no`, `image_slot`으로의 이름 변환은 Silver가 합니다 **[확정, 이광훈 님 동의]**.
  - `run_id`(사진 실행 id)와 `bronze_run_id`는 manifest 맨 위에 한 번만 있고 항목마다는 붙지 않습니다 **[확정, 11/5 이광훈 님 동의]**. B가 처리할 때 해당 실행 ID를 사진 항목과 연결합니다.
  - `files`에는 그 실행에서 새로 저장한 사진만 담고, 업로드가 모두 성공한 칸만 기록합니다. 이미 저장한 사진은 다시 받지 않고 다시 적지도 않으므로, B는 기존 사진 경로를 유지하고 신규·변경된 사진 항목만 반영합니다 **[확정, 11/5 이광훈 님 동의]**. B는 사진 manifest별 처리 이력(run_id, S3 경로, 처리 상태, 처리 시각)을 관리하고, S3의 manifest 목록과 대조해 미처리 실행을 재처리하며, 최초 연동 시 과거 manifest도 처리합니다.
  - `status`는 실행 중 `in_progress`, 끝나면 `ok`, 업로드가 끊기면 `aborted`이고 같은 파일을 덮어씁니다. **[합의, 11/6 · 11/11 재확인, 이광훈 님]** `ok`는 정상 적재, `aborted`는 `files`(업로드가 성공한 사진)를 반영하고 끊김을 확인 대상으로 표시, `in_progress`는 진행 중이면 보류하고 같은 `run_id`의 `ok`나 `aborted`가 생기지 않은 채 오래 남으면 비정상 종료로 보고 `files`를 반영하며 확인 대상으로 표시합니다. 이유: 끊긴 실행에서 성공한 칸은 상태 파일에 기록돼 다음 manifest에 다시 나오지 않기 때문입니다. (이광훈 님 의견 11/5: `ok`만 정상 적재, `aborted`는 실패·재처리 확인 대상, `in_progress`는 처리 안 함 → 11/6 이광훈 님이 `aborted`는 `files` 반영으로 동의, `in_progress`는 정체 판단 기준을 계약에 적어 달라고 요청)
  - **정체 판단 [합의, 11/11 이광훈 님]**: manifest 맨 위에 `updated_at`을 둡니다(구현됨). `generated_at`은 실행 시작 시각 그대로이고, `updated_at`은 manifest를 쓸 때마다 갱신됩니다(`CHECKPOINT_EVERY` = 100칸마다 `in_progress`로, 끝나면 `ok`·`aborted`로). 따라서 `status`가 `in_progress`이고 `updated_at`이 **1시간** 이상 지났으면 정체로 봅니다. 1시간은 기본값이고 B가 설정으로 바꿀 수 있습니다. 근거: 백필 실측(11/11~11/12, EC2 파드, S3 업로드 포함)에서 칸당 약 0.9초, 100칸에 약 90초였습니다. 1시간은 체크포인트 간격의 약 40배라 정상 실행을 정체로 잘못 볼 일이 거의 없습니다. (11/6 로컬 측정은 S3 업로드 없이 100칸에 약 80~100초였습니다.) 최종 상태는 같은 `run_id`의 manifest를 다시 읽어 `ok`·`aborted`로 바뀌었는지로 확인합니다.
  - **첫 전체 수집(백필) [제안, 11/6]**: 한 번에 받지 않고 `--limit`로 나눠 여러 번 실행합니다. 실행마다 `run_id`와 manifest가 따로 생겨, 한 실행이 끊겨도 피해가 그 실행에 한정되고 manifest가 커지지 않습니다. 한 번에 전체(약 4.3만 칸)를 받으면 manifest가 약 17MB가 되고 체크포인트마다 전체를 다시 올립니다(추정). 나누는 단위(칸 수)는 S3 업로드 속도를 잰 뒤 정합니다. 매일 도는 증분 수집은 신규 공고분만 받으므로 해당 없습니다.
  - **사진 교체(같은 칸의 주소가 바뀜) [합의, 11/16 이광훈 님 동의 · A 구현됨(`rescue_photos.py`, `CODE_VERSION` 2026-11-16), B 반영 전]**: 아래 규칙대로 A가 받습니다. **B의 사진 manifest 적재(`load_photo_manifest.py`)는 지금 키를 `popfile<N>.<확장자>`, `<N>.webp`로만 허용해서 버전 2 이상 항목이 들어오면 그 manifest 전체를 오류로 처리합니다.** 그래서 A 변경은 B 반영과 같이 배포합니다(순서: XCom → Silver 연결 → 사진 교체 A·B).
    - **배경 (11/12~11/16 확인)**: 보호소가 사진을 다시 올리면 같은 칸의 `popfileN` 주소가 바뀌고 `updTm`도 바뀝니다. 11/12 수집에서 주소가 바뀐 `success` 칸 12개 중 6개는 실제로 다른 사진(미용 후 재촬영 등)이었고, 6개는 새 주소가 404였습니다. 404 6칸은 11/16에도 그대로이고 옛 주소는 열립니다. 새 주소에 공공 API 사진 서버의 파일이 없는 것이라 A가 받을 방법이 없습니다. 사진은 API가 준 주소로만 받을 수 있습니다.
    - **동작**: 주소가 바뀌고 새 주소가 열리면 덮어쓰지 않고 새 키로 추가 저장합니다. 새 주소가 404면 옛 사진과 옛 기록은 그대로 두고, 처음 404가 난 실행부터 manifest에 "교체됐지만 새 사진 없음"을 표시합니다. 같은 새 주소는 마지막 시도로부터 5일이 지난 실행에서 한 번만 다시 보고, 그때도 404면 그 버전만 멈춥니다(`gave_up`). 주소가 또 바뀌면 새 버전으로 보고 처음부터 시도합니다. (11/18 변경, 아래 "404 재확인")
    - **`version` = 칸별 순번**: 같은 공고·같은 칸에서 주소가 처음 나온 순서대로 1, 2, 3을 붙입니다. 처음 받은 사진이 1입니다. 상태 파일이 "이 칸의 이 주소 = 몇 번"을 기억하므로, 같은 주소는 재실행해도 같은 번호이고 같은 키에 같은 파일을 씁니다(이광훈 님 요청: 재실행해도 같은 버전으로 식별). 받은 시각은 재실행하면 값이 달라지고, `sha256`은 404일 때 정할 수 없어서 쓰지 않습니다.
    - **키**: 버전 1은 지금 키 그대로 `popfile<N>.<확장자>`(백필한 사진은 옮기지 않음), 버전 2부터 `popfile<N>_v002.<확장자>`, `popfile<N>_v003.<확장자>`처럼 세 자리 순번을 붙입니다. 서비스용은 `<N>.webp` → `<N>_v002.webp`, 썸네일은 `<N>_v002_thumb.webp`입니다. 세 자리로 고정해 이름순 정렬이 곧 버전순이고 정규식으로 찾을 수 있습니다(이민혁 님 조건). 키를 덮어쓰지 않으므로 캐시 문제도 생기지 않습니다.
    - **manifest 추가 필드**: `files` 항목에 `source_url`(받은 주소), `version`(정수), `replaces`, `first_seen_at`. 교체됐는데 새 주소가 404면 `failed` 항목에 같은 `source_url`(실패한 주소), `version`, `replaces`, `first_seen_at`과 `reason: "replaced_not_found"`. `failed`의 다른 실패(버전 1의 404 등)도 `source_url`을 남깁니다(11/16 추가: B 이력 테이블이 실패 버전의 주소도 기록하며, 그 주소를 받는 공식 경로가 manifest뿐이라서. #35 리뷰).
      - `replaces`: **직전 주소의 버전**입니다. 직전 버전이 404였어도 그 버전을 가리킵니다(예: v3의 `replaces`는 v2). 그래야 중간 버전이 404여도 변경 이력을 순서대로 따라갈 수 있습니다. 버전 1이면 `null`.
      - `first_seen_at`: 그 주소를 **처음 발견한 시각**으로 고정합니다. 재시도해도, 재실행해도 바꾸지 않습니다. 실제로 저장에 성공한 시각과 다를 수 있습니다.
    - **상태 파일**: 칸 기록 맨 위에 최신 버전(`version`, `replaces`, `first_seen_at` 포함)을 두고, 이전 버전은 `previous` 목록에 둡니다. 11/16 이전 기록(`version`·`previous` 없음)은 옮기지 않고 버전 1로 읽습니다(`first_seen_at`은 `stored_at` 또는 `last_attempt_at`). 주소가 바뀐 칸은 발견한 실행에서 새 버전을 `pending`으로 먼저 기록해, `--limit` 때문에 그날 못 받아도 번호와 최초 발견 시각이 바뀌지 않습니다. 새 주소가 404여도 옛 버전의 `success` 기록은 그대로 남습니다.
    - **최신이 아닌 실패 버전**: 주소가 다시 바뀌면 그 전 버전은 더 시도하지 않습니다(예: v2가 404인 채 v3 주소가 나오면 v2는 `failed`로 남고 v3만 받음).
    - **B Silver (이광훈 님, 11/16)**: 교체 표시가 있고 새 사진의 S3 저장과 해시 확인이 끝났으면 충돌이 아니라 정상 버전 변경으로 처리합니다. 새 주소가 404면 기존 S3 경로와 해시를 유지하고 교체 실패 상태만 기록합니다. 현재 사진은 **최신 정상 저장 버전**입니다. 예: v1 정상 저장 → v2 404 → v3 정상 저장이면, v2가 실패한 동안은 v1을 유지하고 v3 저장이 검증되면 v3으로 바꿉니다. 이미지 테이블(`desertion_no`, `image_slot`)은 이 현재 사진만 두고, 과거 버전은 별도 이력 테이블(`version`, `first_seen_at`, `sha256`, `source_url`, 원본 S3 키 등)로 관리합니다. 스키마는 이 절이 확정되면 맞춥니다.
    - **범위 밖**: 백필 때 이미 `gave_up`이 된 칸 중 옛 주소는 열리는 약 45칸은 다루지 않습니다. 같은 주소에 나중에 파일이 올라오는 경우는 아래 "404 재확인"의 한 번으로만 잡습니다.
  - **404 재확인 [결정, 11/18 · A 구현 `CODE_VERSION` 2026-11-18]**: 버전과 상관없이 404(`not_found`, `replaced_not_found`)는 다음 실행에서 바로 다시 보지 않습니다. 마지막 시도(`last_attempt_at`)로부터 **5일**이 지난 실행에서 한 번만 다시 보고, 그때도 404면 `gave_up`입니다. 서버 오류·시간 초과 등 다른 실패는 지금처럼 다음 실행에서 다시 보고 `MAX_ATTEMPTS`(3)번째에 `gave_up`입니다.
    - **근거**: 11/18까지 재시도로 회복된 칸이 0이었습니다. 404로 포기한 2,350칸 중 1,757칸은 첫 시도(11/11 백필) 뒤 6~7일 지나 다시 시도했는데도 404였습니다(나머지 567칸은 백필 중 같은 날 3번, 26칸은 하루 안에 3번 실패). 다만 신규 공고는 보호소가 사진을 늦게 올릴 수 있어(11/17 멘토링) 바로 포기하지 않고 시간을 두고 한 번 더 봅니다. 실행 횟수가 아니라 날짜로 세는 이유는, 수동 실행이 끼면 횟수 기준 재시도가 하루 안에 끝나 버리기 때문입니다(11/17 신규 404 27칸이 하루 만에 3번을 채움).
    - **B에 미치는 영향**: manifest 형식은 그대로입니다(`failed`는 다시 시도할 칸, `gave_up`은 포기). 기다리는 동안의 실행에는 그 칸이 `failed`에 나오지 않고, `counts.waiting_recheck`에 건수만 나옵니다. 재확인에서 성공하면 같은 버전이 `files`에 나오므로, 교체 404였던 v2는 Silver 이력에서 `failed` → `stored`로 바뀝니다(11/18 이광훈 님과 합의, B 구현 예정).
- **[결정 필요]** 사진 최종 저장 버킷·리전: 현재 시험은 시드니 `ap-southeast-2`의 `rebom-test-01`이고 B팀도 사용 중입니다. 최종 버킷은 이민혁 님이 팀 AWS 계정 설정(11/6) 뒤 서울 `ap-northeast-2`에 만들 예정이라, 그때 옮기고 경로·리전을 맞춥니다. (이민혁, 이광훈)
- **실패 처리 [제안]**: 다운로드가 실패하면 URL을 보존하고 상태(`failed`), 사유, 시도 횟수를 남깁니다(누락 표시).
- 사진 해시는 서동현 님이 기록합니다.
- **[결정 필요]** 몇 장을 받을지, 보호중만 vs 3년치, 원본만 vs 리사이즈본, 종료 공고 사진 보관, 실패 5%·PNG 처리 (서동현 협의)
- **사진 임베딩 (pgvector) [제안, 11/17]**: `db/migrations/001_init.sql`의 `image_embedding` 테이블, 코드는 `src/rebom/embedding/`.
  - **입력**: 사진 manifest(`bronze/rescue/image_runs/.../manifest.json`)의 `files`. (`desertionNo`, `slot`)별 현재 사진은 `version`이 가장 큰 항목이고, 같으면 나중 실행이 이깁니다(위 "최신 정상 저장 버전"과 같은 기준). `failed`는 S3에 파일이 없어 대상이 아닙니다.
  - **검증**: S3에서 받은 원본의 sha256이 manifest 값과 다르면 임베딩하지 않고 건너뜁니다.
  - **보내기 전 [제안, 11/17]**: 칸별 최신 버전만, 같은 sha256은 1번만 보내고 같은 파일을 쓰는 칸은 벡터를 나눠 씁니다(11/17 기준 현재 사진 42,219칸 중 고유 sha256 34,856개, 중복 7,363칸). 빈 사진(회색조 256×256으로 줄여 픽셀 표준편차 < 4.0)은 보내지 않고 다음 실행에서도 다시 보내지 않습니다. 전처리는 EXIF 회전 보정 → RGB → 긴 변 1024 이하(늘리지 않음) → JPEG 품질 90, 요청 1번에 사진 최대 6장(사진마다 Content 1개), 받은 벡터는 L2 정규화합니다(`src/rebom/embedding/prep.py`, 로컬 벤치마크 H1·I2와 같음).
  - **적재 순서 [제안, 11/17]**: 임베딩 결과는 먼저 S3 `gold/image_embedding/model=<모델>/dim=<차원>/run_id=<id>/`에 parquet(사진 칸 1개 = 1행, `source` = api·dup·reuse)와 `summary.json`(건수·시간·건너뛴 sha256)으로 씁니다. 확인(`cli staged`)과 승인 뒤 `cli load`로 RDS에 넣습니다.
  - **S3가 원본, RDS는 서비스용 사본 [제안, 11/17]**: S3에는 모델·차원·전처리별로 모든 벡터를 남기고(모델을 바꿔도 지우지 않음, 되돌리기·재적재에 Gemini 재호출 불필요), RDS에는 서비스 중인 모델의 현재 사진 벡터만 둡니다.
    - **매일 변경분만**: `embed`는 manifest의 현재 sha256 중 S3에 없는 것만 보내고, `load`는 manifest의 현재 sha256과 RDS의 sha256을 비교해 새 칸과 사진이 바뀐 칸만 씁니다. 같은 칸은 건너뜁니다(전처리를 바꿨을 때만 `--full`).
    - **RDS에 남는 옛 벡터**: 교체 전 사진·manifest에서 사라진 칸은 `load`가 개수(`rds_not_current`)만 보여 주고 지우지 않습니다.
    - **Bronze 원본을 못 읽을 때 [11/19]**: 그 사진만 건너뛰고 `summary.json`의 `read_error_keys`에 남겨 다음 실행에서 다시 읽습니다(영구 제외 아님).
    - **모델 교체**: 새 모델 벡터를 S3에 만들고 → RDS에 나란히 넣어 비교하고(기본키에 `model` 포함, 차원이 다르면 새 마이그레이션) → 전환 뒤 옛 모델 행을 RDS에서 지웁니다. RDS에는 많아야 2개 모델만 잠깐 둡니다.
    - **비용 근거 (11/17 실측)**: 768차원 1,461행에 테이블 10.4MB·HNSW 4.6MB, 전체 42,219행이면 약 300MB·130MB(추정). gp3 20GB는 이미 할당돼 있어 디스크 비용은 늘지 않고, 제약은 `db.t4g.micro` 메모리 1GB입니다(HNSW는 메모리에 올라가야 빠름). S3 parquet는 모델 1개에 약 140MB입니다. 3072차원은 4배이고 pgvector HNSW 한도(2,000차원)를 넘어 인덱스를 그대로 만들 수 없습니다.
    - 글(텍스트) 임베딩도 같은 구조(`gold/text_embedding/…`, 카드 해시 기준)로 합니다. 글도 사진과 같은 모델(`gemini-embedding-2`, 768)을 쓰고, 검색에서는 순위로 합칩니다. RDS 테이블 초안은 `db/migrations/002_text_embedding.sql`(`text_embedding`, 기본키 공고·`text_kind`·`model_key`, 아직 적용 안 함). 자세한 계획은 `docs/image_embedding_pipeline.md` 10절.
  - **키**: (`desertion_no`, `image_slot`, `model`). 같은 칸의 sha256이 바뀌면 그 행을 덮어씁니다(과거 버전 벡터는 남기지 않음).
  - **칼럼**: `photo_version`, `sha256`, `s3_key`(원본 키), `photo_run_id`, `embedding vector(768)`, `embedded_at`.
  - **모델**: `gemini-embedding-2`, `output_dimensionality=768`(HNSW 인덱스 한도 2,000차원), 코사인 거리 HNSW 인덱스(`001_init.sql`, 사진 백필이 끝나 그대로 유지). **[확정, 11/24]** 새로 만드는 벡터 테이블(글 `text_embedding` 등)은 v0.1에서 인덱스 없이 전체 비교하고, HNSW는 v0.1 실측 확인 후 v0.2에서 도입을 고려합니다. 종·보호중 조건은 서비스 테이블 `animal`과 `desertion_no`로 JOIN해 거릅니다(벡터 테이블에 종 칼럼은 두지 않음). 사진 HNSW를 타는 검색은 조건으로 걸러진 만큼 결과가 모자랄 수 있으니 `hnsw.ef_search`를 LIMIT 이상으로 두고 `hnsw.iterative_scan`(pgvector 0.8)을 켭니다. 이 설정은 성능 비교 후 검색 코드에 넣습니다(지금 코드는 기본값). 모델·차원을 바꾸면 새 마이그레이션으로 칼럼이나 테이블을 추가합니다.
  - **[확정, 11/19]** 종료 공고 사진의 벡터는 지우지 않고 검색에서 거릅니다. 종료 뒤 보호중으로 복귀하는 공고가 있기 때문입니다(3절). 검색은 기본으로 `processState`가 `보호중`인 공고 안에서만 찾습니다. 공고 상태는 B 서비스 테이블에서 읽습니다.

## 7. 컬럼 (A→B 인계 스키마 v0)

원본은 응답 그대로 넘기고, 아래는 B가 알아야 할 역할과 정제 대상입니다. 전체 목록은 노션 "구조 API 요청 조건·응답 필드 정리"를 따릅니다.

| 컬럼 | 형식 | 역할 | 정제·주의 |
| --- | --- | --- | --- |
| `desertionNo` | 문자열 | 고유키 | |
| `noticeNo` | 문자열 | 보조 식별 | |
| `happenDt`, `noticeSdt`, `noticeEdt` | `YYYYMMDD` 문자열 | 날짜 | 날짜 타입으로 |
| `updTm` | `YYYY-MM-DD HH:MM:SS.0` | 수정 시각 | 타임스탬프로. 변경 비교에서는 제외 |
| `processState` | 문자열 7종 | 상태 | 보호중 / 종료(입양·반환·자연사·안락사·기증·방사) |
| `endReason` | 자유 문장 | 종료 사유 보조 | 종료 공고의 28%만 값이 있음. 구분에는 `processState` 사용 |
| `upKindCd`, `upKindNm` | 코드·이름 | 축종 | 개 417000, 고양이 422400, 기타 429900 |
| `kindCd`, `kindNm`, `kindFullNm` | 문자열 | 품종 | 기타 축종은 `kindFullNm`에만 실제 이름. 표기 통일 필요 |
| `colorCd` | 자유 기재 | 색상 | 표준 색상으로 통일 |
| `age` | `2026(년생)` | 나이 | 출생연도 정수로 |
| `weight` | `13(Kg)`, `0.8~0.9(Kg)`, `330g(Kg)` | 체중 | 단위·범위가 섞여 kg 숫자로 보정 |
| `sexCd`, `neuterYn` | M/F/Q, Y/N/U | 성별, 중성화 | |
| `specialMark` | 문장 | 검색 재료 | 28%가 9자 이하 |
| `popfile1`~`popfile8` | URL | 사진 | 값이 있을 때만 응답에 나옴. 변경 감지는 내용 비교 |
| `happenPlace`, `orgNm` | 문자열 | 장소 | |
| `careRegNo`, `careNm`, `careTel`, `careAddr`, `careOwnerNm` | 문자열 | 보호소 | 개인정보 검토 필요(아래 9) |
| `healthChk`, `vaccinationChk` | 체크박스 문자열 | 건강·접종 체크(명세) | 9.9%·11.5%만 채워짐. 원본 그대로 보존 |
| `sfeSoci`, `sfeHealth` | 문자열 | 특징(사회성), 특징(건강) | 각 1.5%만 채워짐. 원본 그대로 보존 |
| `etcBigo`, `rfidCd`, `adptn*`, `sprt*`, `srvc*`, `evnt*` | 문자열 | 특이사항(내용 0건), 동물등록번호(5.7%), 보호소 안내문(0.5%) | 버리지 않고 원본 그대로 보존 |

- 값이 비어 있으면 **키 자체가 안 오는** 필드가 있어서 공고마다 필드 개수가 조금 다릅니다. B는 누락을 허용해야 합니다(null이 아닌 "키 없음").

## 8. 예외 레코드 처리 [제안]

- 응답에 빈 객체가 섞이거나 `desertionNo`가 없는 항목은 **버리지 않고 건수를 manifest에 기록**하고, 필요하면 격리 파일로 따로 보관합니다.
- `totalCount`와 실제로 받은 건수가 다르면 manifest에 경고를 남깁니다. **[확정]** 구조 API의 `totalCount`는 믿을 수 있음(월별 합계와 연 단위 값이 일치).
- **상태가 없는 공고 [제안]**: 보호중 목록에서 온 공고에 `processState`가 없으면 보호중으로 보고 품질 경고를 남깁니다(2013년 발생 1건 관찰). B는 수집 출처(보호중 목록)를 근거로 보호중으로 보정하되 원본 결측 여부와 품질 경고를 함께 남깁니다(이광훈 동의). 공고 종료일이 발생일보다 5년 뒤인 날짜 오류 의심 1건도 있었으나 영향은 작습니다.
- 11/2 시험에서 `protect` 2023~2025 구간은 `totalCount` 4,796건인데 4,793건만 받아졌고(반복해도 같음) 원인은 모릅니다. 이런 구간은 위 3절의 종료 판정 조건을 따릅니다.
- 페이지를 넘기는 동안 상태가 바뀌어 일부가 빠질 수 있다는 점은 추정이고 관찰하지 못했습니다. 기간을 작게 나눠 받고 `desertionNo`로 중복을 거릅니다.

## 9. 개인정보·보호소 필드 [대표자명 확정, 전화번호 표시는 결정 필요]

- **[확정]** `careOwnerNm`(대표자명)은 저장하지 않습니다(팀 규칙 AGENTS.md). 증분 수집 코드가 저장 전에 제거하고 manifest `dropped_fields`에 기록하며(이광훈 동의), 보호소명·주소·전화는 유지합니다. 수집본 기준 보호소 310곳 중 이름 형태 279곳, 휴대전화(010) 번호 35곳이라 서비스에 표시하기 전에 확인이 필요합니다.
- 지금 받아 둔 파일(`data/raw`)은 그대로 두고, Airflow·적재 때 반영합니다.
- 인증키와 신고자 정보는 저장·로그·오류 메시지에 남기지 않습니다(오류는 예외 종류만 기록).

## 10. 종료 공고

- 서비스 테이블에는 넣지 않고 원본·silver에만 보관합니다. **[확정, 10/30]**
- 처음부터 종료로 올라오는 공고(약 11%)는 수정일 증분으로 받아 보관합니다(A안). **[확정]**
- 종료 공고는 다시 조회하지 않습니다(3일간 변경 0.2%, 대부분 비고·사진 칸). **[확정]**
- 사라진 공고의 종료 사유는 받지 않고 "종료(사유 미상)"으로 표시합니다(10/31 결정). 사유가 필요해지면 발생일 또는 `desertion_no`로 조회합니다.

## 11. 합의할 것 (체크리스트)

- [x] S3 업로드 책임: A가 원본 JSONL.gz와 manifest를 올리고, B가 읽어 Silver 변환·적재 (이광훈 동의, PR #4 리뷰)
- [x] 원본 경로·파티션 규칙, 실행마다 새 경로(덮어쓰지 않음), manifest 필드, `fetched_at`·`run_id` 형식 (이광훈 동의, 한 실행에서 같은 `run_id` 사용, PR #4 리뷰)
- [ ] 변경 판정: 내용으로 판정(`updTm` 제외), 방식은 B가 선택. 임베딩 갱신 대상 필드 (서동현, B). 내용 기반 판정 구현은 B가 확인함(PR #4 리뷰)
- [ ] 상태 이력 테이블 구조 (B). 보호중 복귀 허용은 B가 확인함(PR #4 리뷰)
- [x] 보호소 대표자명(`careOwnerNm`)은 저장하지 않음 (팀 규칙 AGENTS.md). 전화번호 표시 범위는 서비스 쪽에서 결정
- [x] 사라진 공고의 종료 사유: ④ + 필요할 때 ①로 결정(10/31)
- [ ] 사라짐 이벤트 단계를 Silver로 분리(경로 `silver/rescue/events/...`), 이벤트 필드 `state_source`, 상태 값 읽는 순서(`lookup`·`relookup` > `updated` > `none`), B 로더의 필수 파일 변경 (이광훈, B). 3절
- [ ] 보호중 목록의 발생일 범위: 날짜 제한 없이 3년 이하 구간으로 나눠 전부 받기, 7/1 이전 약 9,740건 (11/2 정정)
- [ ] 사진 처리: 몇 장, 저장 범위, 원본/리사이즈 (서동현)
- [x] 사진 교체: 칸별 순번 `version`, 키 규칙, manifest 필드(`source_url`, `version`, `replaces`, `first_seen_at`, `replaced_not_found`), Silver 현재 사진 기준 (6절, 11/16 이광훈 님 동의). 구현은 #27 → XCom → Silver 연결 뒤 별도 PR
- [ ] 정제 규칙 책임: 체중·나이·색상·기타 축종 이름 (B)
- [ ] 날짜·시각 형식과 시간대 통일: `fetched_at`·`run_id`는 한국 시간으로 합의(PR #4). `updTm`과 정제 후 타임스탬프의 시간대는 미정
- [x] 종료 판정은 결과 코드 00, 구간 1,095일 이하, 받은 건수 = `totalCount`일 때만. 어긋난 구간의 부재는 `desertion_no`로 확인 후 판정하고, 개별 조회까지 실패하거나 불확실하면 기존 상태를 유지한 채 재처리 대상으로 남김 (이광훈 동의, PR #4 리뷰)
- [x] 마지막 성공일을 하루 겹쳐 받기(B가 `desertion_no` 기준 UPSERT와 내용 비교로 거름) (이광훈 동의, 11/2)
- [x] manifest `partitions` 필드 이름과 날짜 형식, 사라짐 이벤트(`confirmed_by`)를 `confirmed_missing_ids`로 받기 (이광훈 동의, 11/2)

## 12. 근거

- 노션: 구조 API 검증 결과 (2.2 수집본 비교, 2.3 수정일 검증, 2.4 사진, 2.7 증분 시험, 2.8 종료 공고)
- 노션: 구조 API 요청 조건·응답 필드 정리

## 13. 사진 태그 (Gold, 모델 결과) [test 환경, 2026-11-20]

보호중(`status_group = PROTECTING`) 개(`species_code = 417000`)의 대표 사진 한 장에 털·귀·눈매·닮은 품종 태그를 붙여 S3 Gold(Iceberg, Glue DB `rebom_gold`)에 씁니다.
코드는 `src/rebom/tagging/`, 테이블 정의는 `src/rebom/common/gold_catalog.py`, 실행은 `dags/photo_tags.py`입니다. 연계 위치는 수집 DAG 의 Silver task 뒤로 정했지만, 지금 Silver 가 dry run 이라 연계 실행은 실제로 이루어지지 않고 손으로만 실행합니다(Silver apply 전환 뒤 Airflow Asset 으로 연결).

### 13.0 공용 키 (subject_type, subject_key) [2026-11-20 결정]

Gold 모델 결과 테이블은 공고번호 대신 **무엇을 처리했는지**를 두 칸으로 가리킵니다. 사진 태깅·글 태깅·임베딩이 이 키를 같이 씁니다. 처리 기록·요청 기록 테이블은 묶음마다 따로 둡니다(태깅 13.3, 임베딩 13.4. 2026-11-21 결정).

| `subject_type` | `subject_key` | 쓰는 곳 |
|---|---|---|
| `photo` | 사진 원본 sha256 | 사진 태깅, 사진 임베딩 |
| `animal` | 공고번호(`desertion_no`) | 글(특이사항) 태깅·글 임베딩, 사진 없음(`NO_PHOTO`) 기록 |

- 사진은 sha256 단위라 **같은 사진이 여러 공고에 올라와도 한 번만 부릅니다.** 공고–사진 연결은 Gold 에 두지 않고 Silver 현재 사진 테이블(`desertion_no`, `image_slot`, `sha256`)에서 합니다.
- 비용은 요청 기록의 `cost_usd` 에 OpenRouter 가 돌려준 `usage.cost` 를 그대로 넣습니다(태깅 `gold_model_request_log`, 임베딩 `gold_embedding_request_log`. 같은 칸).

### 13.1 대상과 다시 태깅하는 조건

- 대표 사진: Silver 이미지 테이블에서 `original_s3_key` 가 있는 가장 작은 `image_slot`. 키는 Silver 값을 그대로 쓰고(`_v002` 같은 교체 사진 포함), 받은 사진의 sha256 이 Silver 값과 다르면 그 사진은 실패(`SHA_MISMATCH`)로 둡니다.
- 보호중 개들의 대표 사진을 sha256 으로 묶어 사진 단위로 부릅니다. 다른 공고에서 이미 태깅한 사진이면 부르지 않습니다.
- 사진이 없는 개는 `NO_PHOTO` 로 한 번만 기록합니다(`subject_type = animal`, `subject_key` = 공고번호, `input_hash = "no-photo"`). 나중에 사진이 생기면 그 사진(sha256)이 새 대상이 됩니다.
- 다시 부르는 경우는 둘뿐입니다. 공고의 대표 사진이 아직 태깅하지 않은 sha256 으로 바뀌었을 때, 질문지 버전(`version`)이 바뀌었을 때. 실패(`FAILED`)는 3번까지 다시 시도합니다.
- 모델: OpenRouter `google/gemini-3.1-flash-lite`, 공급자 `google-ai-studio` 고정(대체 공급자 없음), reasoning low, `media_resolution = MEDIA_RESOLUTION_MEDIUM`, temperature 1.0(평가 때 Google 기본값과 같은 값을 명시). seed 는 보내지 않습니다. 2회 답의 일치로 판정하므로 두 회차가 독립이어야 합니다.
- 실행을 멈추는 경우: 특징 질문 하나의 prompt_tokens 가 1,500 을 넘을 때(해상도 설정이 빠진 것으로 봄, medium 약 1,294, high 약 1,818), 그리고 키·계정 오류(401, 402, 검열 표시가 없는 403, 키 한도)일 때. 계정 오류를 사진 실패로 세면 모든 대상의 시도 횟수가 쌓여 3번 뒤 영구히 건너뛰기 때문입니다.
- 사진 크기: 10MB 를 넘는 원본은 보내지 않고 실패(`IMAGE_TOO_LARGE`)로 둡니다.
- 질문지: 특징 `trait-vision-v10`(작업 `photo_trait`), 품종 `breed-vision-v2-open`(작업 `photo_breed`). 같은 사진에 작업마다 2번 묻습니다(사진당 요청 4번, 약 $0.0033).

### 13.2 태그와 최종 판정 기준

모든 태그는 2번의 답이 같을 때만 붙입니다. 중간·판단 어려움·안 보임 답은 태그를 만들지 않습니다.
인상(귀염·늠름)은 태깅하지 않으며, '귀염'은 어떤 경우에도 검색 태그로 만들지 않습니다.

| 태그 코드 | 이름 | 판정 기준 | 검색 | 화면 |
|---|---|---|---|---|
| `fur.fluffy` | 복슬복슬 | 털이 길거나 곱슬·부풀어 몸 윤곽이 털에 가려짐. 2회 모두 '복슬복슬' | O | O |
| `fur.short` | 짧은 털 | 털이 짧아 몸 윤곽이 보임(짧고 촘촘한 이중모 포함). 2회 모두 '매끈' | O | O |
| `ear.drop` | 늘어진 귀 | 귀 전체가 머리 옆으로 처져 내려옴. 2회 모두 '늘어짐' | O | O |
| `ear.semi` | 반쯤 선 귀 | 귀뿌리는 서 있고 끝이 앞이나 옆으로 접힘. 2회 모두 '반쯤 섬' | O | O |
| `ear.prick` | 선 귀 | 귀가 끝까지 위로 곧게 섬. 2회 모두 '섬' | O | O |
| `eye.round` | 둥근 눈매 | 크고 동그란 눈. 2회 모두 '둥글함' | O | O |
| `eye.sharp` | 날카로운 눈매 | 눈이 가늘고 눈꼬리가 길거나 올라감. 2회 모두 '날카로움' | O | O |
| `breed.<품종>` | 닮은 품종 | 두 회차 모두 Top3 에 든 품종을 한 행씩 둡니다(`rank`, `similarity` = 두 회차 평균). 평균 유사도 90 이상(강 신호)만 검색에 씁니다 | 강 신호만 | X |

품종은 모델이 세계의 모든 품종 중에서 자기 지식으로 고릅니다(특징표 없음, 11/17 결정). 3년치 공고 빈도로 고른 62종(`src/rebom/tagging/data/breed_candidates_3y.json`)은 질문지의 참고 목록이고, 모델이 같은 품종을 다르게 쓴 이름(진돗개 → 진도견, 토이푸들 → 푸들 등)을 한 표기로 맞추는 데만 씁니다. 목록 밖 품종은 모델이 쓴 이름 그대로 태그가 됩니다. 같은 품종 안의 크기·계통 차이(푸들·닥스훈트 크기, 저먼 셰퍼드 독 → 셰퍼드 등)만 하나로 묶고, 테리어처럼 품종 그룹으로는 묶지 않습니다. 모델이 쓴 원래 이름은 `detail` 의 `raw_breed` 에 남습니다.

품종 태그는 강 신호(믹스견 포함)라도 화면에 보이지 않고(`displayable = false`) 자연어 검색·챗봇에만 씁니다(11/17 결정). 화면의 품종은 보호소가 공고에 적은 `kindNm` 이며 태깅 결과가 아닙니다.

### 13.3 테이블

| 테이블 (test 는 이름 뒤 `_test`) | 한 행 | 쓰는 방식 |
|---|---|---|
| `gold_animal_tag` | 대상 × 작업 × 질문지 버전 × 태그 | 같은 대상·같은 질문지 버전으로 다시 태깅했을 때만 (`subject_type`, `subject_key`, `task`, `prompt_version`) 범위의 행을 교체. 사진이 바뀌면 새 sha256 이 새 행이 되고, 질문지 버전을 올려도 이전 행은 남음(이전 버전 보존). 실패한 작업은 교체하지 않음 |
| `gold_inference_log` | 대상 × 작업 × 버전 × 입력 해시 | (`subject_type`, `subject_key`, `task`, `version`, `input_hash`) 로 upsert. 사진은 `input_hash` = sha256. `status` = OK·NO_RESULT·FAILED·NO_PHOTO, `attempts`, `tag_count`, `last_error`. `status`·`tag_count` 뜻은 아래 표 |
| `gold_model_request_log` | 모델 요청 한 번 | append 만. 대상은 `subject_type`·`subject_key`. `dt`(요청 UTC 날짜) 파티션. 토큰·비용·지연·공급자·응답 모델·원문 답, `temperature`·`reasoning_effort`, `finish_reason`·`native_finish_reason`(빈 답·안전 차단·잘림 구분). 한 행 = API 호출 한 번. 사진 태깅은 늘 한 장씩 부르므로 `batch_size` 칸을 두지 않고 1장으로 읽습니다(`request_scope`·`item_count` 는 두지 않음, 11/23 결정) |

`gold_inference_log` 의 `status`·`tag_count` 뜻(OK·NO_RESULT 는 둘 다 끝난 것으로 보고 다시 부르지 않습니다):

| 작업 | OK | NO_RESULT | `tag_count` |
|---|---|---|---|
| `photo_trait` | 태그 행이 하나 이상 | 2회 답이 맞는 요소가 하나도 없음(태그 행 없음) | 태그 행 수(모두 검색용) |
| `photo_breed` | 강 신호(평균 유사도 90 이상) 품종이 하나 이상 | 강 신호 없음. 두 회차 모두 Top3 에 든 품종이 있으면 검색에 안 쓰는 품종 행(`searchable = false`)은 남아 있음 | 강 신호(검색용) 행 수 |

그 밖에 `FAILED`(호출·답 형식 오류, 3번까지 다시 시도), `NO_PHOTO`(사진 없는 공고, `subject_type = animal`)가 있습니다.

`gold_animal_tag` 칸: `subject_type`, `subject_key`, `tag_code`, `source`(출처: photo 사진 · text 공고 글 · rule 공고 칸. 모델 이름은 넣지 않고 `model`·버전으로 구분, 11/24), `tagger_version`, `task`, `model`, `prompt_version`, `source_hash`(입력 해시, 사진은 sha256), `searchable`, `displayable`, `rank`, `similarity`, `detail`(근거 JSON), `gold_run_id`, `gold_updated_at`.

- 위치: test `s3://rebom-data-559987919299/gold/_test/iceberg/`, prod `s3://rebom-data-559987919299/gold/iceberg/`. 실행 중간 파일(대상 목록)은 `gold/_work_test/photo_tags/<run_id>/targets.json`.
- 테이블 만들기는 클라우드 리소스를 만드는 일이라 사람이 직접 실행합니다: `python -m rebom.common.gold_catalog --env test --create --profile rebom`. 이미 있는 테이블에는 빠진 칸을 더합니다. 칸이 없으면 새 값이 소리 없이 버려지므로, 칸을 늘린 코드를 돌리기 전에 먼저 실행합니다. `subject_type`·`subject_key` 가 없는 옛 테이블(11/20 이전 `desertion_no` 키)은 칸 추가로 맞추지 않고 멈추며, test 는 `--create --recreate` 로 지우고 다시 만듭니다(옛 행은 버림, prod 는 거부). 파드(노드 역할)는 읽기·쓰기만 합니다.
- 요청 기록 표는 작업 묶음별로 따로 둡니다. 태깅은 `gold_model_request_log`, 임베딩은 `gold_embedding_request_log`(공통 칸 + `batch_size`, 13.4).
- 서비스 DB 적재(pgvector 쪽 태그 테이블)는 별도 작업입니다.
- 이전 버전 보존(2026-11-20 결정): `gold_animal_tag` 는 사진이 바뀌거나 질문지 버전을 올려도 이전 행을 지우지 않습니다. 오래된 버전 정리는 공고·이미지 정리 기준이 정해진 뒤에 따로 합니다.
- 서비스 DB 에 넣을 행(활성 태그): Silver 에서 공고별 대표 사진을 구해 사진 태그와 잇습니다. 아래 조건을 모두 만족하는 행만 고릅니다.
  1. 공고 `status_group` 이 보호중
  2. 그 공고의 대표 사진(Silver 현재 사진 테이블에서 키가 있는 가장 작은 칸)의 sha256 = 태그의 `subject_key` (`subject_type = photo`)
  3. `prompt_version` 이 작업별 현재 질문지 버전(`run_photo_tags.VERSIONS`: `photo_trait` = `trait-vision-v10`, `photo_breed` = `breed-vision-v2-open`)
- 남아 있는 태그: `gold_animal_tag` 는 이전 사진·이전 버전·보호가 끝난 공고의 사진 태그를 지우지 않습니다. 위 조건으로 걸러서 씁니다.


### 13.4 임베딩 묶음 [2026-11-20 결정, 11-21 기록 표 분리 · test·prod 표 만들고 옛 parquet 옮김, 11-23 DAG 쓰기 전환 PR]

사진·글 임베딩 결과도 Iceberg `rebom_gold` 로 옮깁니다. 태깅(Jev·vLLM)과는 다른 표 묶음이고, **처리 기록·요청 기록도 임베딩 묶음 안에 따로** 둡니다(11-21 결정). 칸 이름은 태깅 표를 따릅니다. 표 정의는 `src/rebom/common/gold_catalog.py`, 검토 문서는 `docs/gold_rules_embedding_review_20261120.md`.

| 테이블 (test 는 이름 뒤 `_test`) | 한 행 | 키 (upsert) |
|---|---|---|
| `gold_image_embedding` | 사진 1장(sha256) × 모델 × 전처리 버전 | `subject_type`(photo), `subject_key`(sha256), `task`(image_embedding), `model`, `prompt_version` |
| `gold_text_embedding` | 공고 글 카드 × 모델 × 템플릿 버전 × 카드 해시 | `subject_type`(animal), `subject_key`(공고번호), `task`(text_embedding), `model`, `prompt_version`, `source_hash` |
| `gold_embedding_canary` | 모델 변화 확인(canary) 1번 | `gold_run_id`(`canary-<시각>`) |
| `gold_embedding_inference_log` | 대상 × 작업 × 버전 × 입력 해시 | `gold_inference_log` 와 같은 칸·키 + `detail` |
| `gold_embedding_request_log` | 요청 1번 (매일 실행은 사진 1장씩이라 = 사진 1장) | `gold_model_request_log` 와 같은 칸(append, `dt` 파티션) + `batch_size` |

- `model` = 모델 이름과 차원, 예 `gemini-embedding-2:768`(코드의 `MODEL_KEY`). 이름의 숫자가 모델 버전이고, 새 버전(`gemini-embedding-3`)은 다른 모델이라 따로 백필·검증·전환합니다. 같은 이름 안의 변화는 canary 로 봅니다. 파티션은 `model`.
- `prompt_version`: 사진은 전처리 버전(`exif-rgb-max1024-jpeg90`), 글은 템플릿 버전(`A2`).
- `source_hash`: 사진은 원본 sha256(`subject_key` 와 같음), 글은 가린 카드 본문의 sha256(전화번호 `[번호]`, 앞 관리번호 지움, 템플릿 적용 뒤).
- 글은 모델에 넣은 가린 카드 본문을 `input_text` 로 남깁니다(글 벡터 성능 평가에 씀). 카드를 만든 Silver 행의 `source_updated_at_raw` 를 `source_update_dt` 로 둡니다. 카드가 바뀌면 새 행이 생기고 이전 행은 남습니다.
- 벡터는 `embedding list<float>`(768개, float32).
- 공고–사진 연결(`desertion_no`, `image_slot`)은 Gold 에 두지 않습니다(13.0). 지금은 Bronze 사진 manifest, Silver 운영 뒤에는 Silver 현재 사진 테이블에서 합니다. RDS `image_embedding` 은 지금처럼 칸당 1행입니다.
- 기록 표를 나눈 이유: 임베딩만 쓰는 칸(`detail`, `batch_size`)을 태깅 표에 더하지 않고, 묶음마다 표를 따로 만들고 고칠 수 있게 하기 위해서입니다. 칸 이름·타입은 태깅 기록 표와 같아서, 전체 비용 등은 Athena 에서 두 표를 `UNION ALL` 로 합쳐 봅니다.
- 처리 기록(`gold_embedding_inference_log`): `version` = `<model>/<prompt_version>`(태깅 처리 기록과 같은 키라 모델 칸이 없어서 모델을 함께 넣음). `status` = OK·BLANK·SHA_MISMATCH·READ_ERROR·FAILED. 빈 사진은 `detail` 에 판정 점수 `{"gray_std": …, "blank_std": 4.0}` 를 남겨, 기준을 바꿔도 사진을 다시 받지 않고 다시 판정할 수 있게 합니다.
- 요청 기록(`gold_embedding_request_log`) [11-23 결정]: 태깅 요청 기록과 같은 뜻으로 **요청 1번 = 1행**입니다. 매일 실행(11-24 03:00부터)은 사진을 1장씩 보내 요청 1행 = 사진 1장이고, `batch_size` = 1, 비용·토큰은 요청 값 그대로입니다(장당 과금이라 묶어도 비용은 같음, 11-21 실측 장당 $0.000116). 전체 재임베딩처럼 시간이 중요할 때만 `--image-batch 6` 으로 묶고, 그때도 요청 1행(`subject_key` = 첫 사진, `batch_size` = 장수, 비용은 요청 전체)이며 사진 → 요청 연결은 처리 기록 OK 행의 `detail` `{"request_id", "batch_size"}` 로 합니다. canary 도 1장씩 6번 보내 6행입니다(`gold_run_id = canary-<시각>`).
- 쓰는 곳(11-23 전환): `embed --gold-env prod` 가 결과·처리 기록·요청 기록 표에 쓰고, `load --gold-env prod` 가 결과 표를 읽어 RDS 에 넣고, `canary --gold-env prod` 가 canary 표와 요청 기록 표에 남깁니다(`src/rebom/embedding/gold_store.py`). embed 는 되돌릴 때를 위해 S3 parquet 도 같이 씁니다(읽지 않음). 처리 기록의 `READ_ERROR` 는 `detail` 에 Bronze 키를 두고 `attempts` 로 연속 실패 수를 셉니다.
- 환경: DAG 매일 실행은 **prod 에만** 씁니다. test 는 표 구조·옮기기·조회 확인용이고 DAG 가 쓰지 않습니다. 임베딩 입력은 Bronze manifest 라 Silver prod 와 관계없이 Gold prod 를 만들 수 있습니다.
- 표 만들기(사람이 실행): test `python -m rebom.common.gold_catalog --env test --create --profile rebom`, prod `python -m rebom.common.gold_catalog --env prod --create --group embedding --profile rebom`(임베딩 묶음 표 5개만. 태깅 표는 만들거나 바꾸지 않음).
- 종료 공고(11-20 결정): 종료 뒤 **30일**이 지나면 `load` 가 RDS 벡터를 지웁니다. Bronze·Gold 에는 남아 있어 복귀하면 Gemini 를 다시 부르지 않고 다시 넣습니다. RDS 쓰기라 승인 뒤에 켭니다.
- 활성 버전: 코드 상수(`gemini.MODEL`·`DEFAULT_DIM`, `prep.PREP_VERSION`, 글 템플릿). 바꾸면 PR 로 남깁니다.

### 13.5 Gold 공용 기록 스키마 [초안, 2026-11-22]

태깅·임베딩의 대상은 Silver에서 고릅니다. 사진은 Silver 현재 사진에 기록된 S3 키로 Bronze 원본을 읽습니다. 사진 임베딩에서 manifest를 직접 읽는 방식은 임시입니다.

| 테이블 | 한 행의 의미 | 키와 쓰기 방식 |
|---|---|---|
| `gold_inference_log` | 입력 하나에 대한 작업의 처리 상태 | 기존 키 `(subject_type, subject_key, task, version, input_hash)`로 upsert |
| `gold_model_request_log` | 모델 호출 한 건의 응답·사용량·오류 | append. `requested_at`의 UTC 날짜인 `dt`로 파티션. 공급자 `request_id`는 응답에서 받을 수 있을 때 저장 |

**처리 기록 필드안**

| 필드 | 타입 | 의미 |
|---|---|---|
| `subject_type`, `subject_key` | string, 필수 | `animal`은 공고번호, `photo`는 원본 사진 sha256. 배치로 보낸 항목도 각 대상 키로 기록 |
| `task`, `version`, `input_hash` | string, 필수 | 작업, 결과 재사용을 구분하는 버전, 실제 모델 입력의 해시. 사진 원본을 그대로 쓰는 작업은 원본 sha256. 글은 개인정보를 가리고 전처리한 모델 입력을 해시 |
| `status`, `attempts`, `tag_count`, `last_error` | string, int, int, string | 기존 처리 상태와 시도 횟수, 태그 수, 마지막 오류. 임베딩은 `tag_count`를 비움 |
| `gold_run_id`, `gold_updated_at` | string, timestamptz | 처리 배치와 상태 갱신 시각 |

모델·질문지·전처리의 변경이 결과에 영향을 주면 재사용 키의 `version`도 바뀌도록 합니다. 기존 사진 태깅 버전과 공용 버전의 생성 규칙은 구현 전 확인합니다.

**호출별 요청 기록 [11/23 결정]**

- 호출당 한 행에 비용·토큰·지연을 기록합니다. 사진 태깅은 한 장씩 호출합니다. 임베딩은 매일 한 장씩 보내고 재임베딩에서 최대 여섯 장을 묶을 수 있습니다.
- 임베딩 요청은 `subject_type=photo`, `subject_key=첫 사진의 SHA-256`, `batch_size=실제 전송 수`를 저장합니다. 사진별 처리 행의 `detail.request_id`로 해당 호출을 연결합니다. 태깅 요청 로그는 `batch_size`가 없으므로 한 장으로 해석합니다.
- 두 요청 로그의 기존 `response_model`과 `provider`는 응답에서 확인한 값이 있을 때 기록합니다. SDK 재시도를 별도 호출로 집계하는 정책은 후속 협의 사항입니다.

### 13.6 Gold 버전 레지스트리

`rebom_gold.gold_version_registry`는 작업별 실행 설정을 보관합니다. 논리 키는 `(task, version)`이며, 버전은 `image_embedding@1`처럼 `task@N`을 사용합니다. `config_json`에는 `model_id`와 해당 작업의 프롬프트·전후처리·벡터 차원 등 실행 설정을 넣습니다. `config_hash`는 키를 정렬하고 불필요한 공백을 제거한 UTF-8 JSON의 SHA-256이며, `registered_at`은 최초 등록 시각입니다. 설정 JSON에는 비밀값과 개인정보를 넣지 않습니다.

같은 설정으로 등록을 반복하면 기존 행을 유지합니다. 설정이 바뀌면 새 버전을 등록합니다. 읽을 때는 저장된 해시를 검증합니다. 먼저 관리자가 `gold_catalog --create --group registry`로 테이블을 만들고, `python -m rebom.common.gold_version_registry --env test --task image_embedding --version image_embedding@1 --config config.json`으로 등록합니다. `--config`를 생략하면 등록값을 조회합니다.

사진 태깅·사진 임베딩은 등록 버전을 명시하면 결과와 처리·요청 로그에 해당 버전을 기록합니다. 사진 입력의 식별값은 이번 연결에서 기존과 같이 검증한 원본 SHA-256입니다. 전처리 변경은 등록 버전으로 구분합니다. 사진 태그의 논리 키는 `(subject_type, subject_key, task, version, tag_code)`, 사진 벡터는 `(subject_type, subject_key, task, version)`입니다. 태그가 0개면 해당 버전의 기존 태그를 지우고 처리 기록을 남깁니다.

글 임베딩의 후보 실행은 `gold_text_embedding.version`과 카드 본문 `source_hash`로 동일 공고의 기존 벡터를 찾습니다. `prompt_version`은 카드 템플릿, `tag_versions`는 카드 생성에 사용한 작업별 태그 버전의 JSON입니다. 같은 버전·같은 본문이면 재호출하지 않습니다. Jev가 태깅한 입력 글의 해시와 A2 카드 전체 본문의 해시는 각각 별개입니다.

기존 결과의 새 `version` 컬럼은 null로 두고, 기존 질문지 또는 모델·차원·전처리 키로 계속 읽습니다. 이전 행을 새 버전으로 이관하려면 실제 설정을 확인해야 합니다. 필드가 추가된 테이블을 먼저 적용하고 초기 버전을 등록한 후 후보 실행을 사용합니다. Silver `publish_id` 연결과 RDS `active_version` 전환은 별도 구현입니다.

단계별 연동과 검증 항목은 [Gold 스키마 구현 기준](gold_schema_design.md)을 참고합니다.

## 14. 현재 사진 스냅샷 [합의, 2026-11-20 이광훈 님 · A 구현 `CODE_VERSION` 2026-11-20]

사진 manifest는 그날 처리한 칸만 담는 변경분이라, 하루를 빠뜨리면 Silver가 칸의 현재 사진을 알 수 없습니다(11/19·11/20 실패). 그래서 A가 사진 실행이 `ok`로 끝날 때마다 **오늘 보호중 공고의 모든 칸의 현재 상태**를 함께 씁니다. 원천은 A 상태 파일이고, Silver의 입력입니다(기준 데이터는 Silver).

- **[확정, 11/20 이광훈 님]** Silver 현재 사진 테이블은 검증된 스냅샷 기준으로 갱신하고, 버전 이력 테이블은 지금처럼 manifest로 유지합니다.

### 14.1 경로와 쓰는 순서

- `bronze/rescue/image_current/dt=<실행일>/run_id=<사진 실행 id>/current.jsonl.gz` (한 줄 = 한 칸, `desertion_no`·`slot` 순 정렬)
- 같은 폴더의 `snapshot.json`을 **마지막에** 씁니다. `snapshot.json`이 있으면 `current.jsonl.gz`는 다 쓰인 것입니다.
- 사진을 다 받았을 때만 씁니다. 끊긴 실행·dry run에는 쓰지 않습니다.
- **manifest가 `ok`이면 스냅샷이 반드시 있습니다 [11/20 결정].** 순서: 받은 기록을 manifest `in_progress`로 저장 → 스냅샷 검증·쓰기 → manifest `ok`. 스냅샷에서 실패하면 manifest를 `aborted`로 두고 맨 위에 `abort_reason`(`snapshot: ...`)을 남긴 뒤 사진 task를 실패시킵니다. 받은 사진(`files`)은 기존 `aborted` 규칙대로 반영할 수 있습니다.

### 14.2 줄 범위와 `slot_state`

- 오늘 목록에 있는 공고 중, 상태 파일에 버전 기록이 있는 칸만 나옵니다(`--limit`로 아직 한 번도 시도하지 않은 칸은 없음).
- `active`: 오늘 목록에 그 칸의 주소가 있음. `emptied`: 기록은 있지만 오늘 주소가 없음(보호소가 사진을 뺌). `emptied`는 현재 사진 칸이 모두 null입니다.
- **`emptied` 처리 [확정, 11/20 이광훈 님]**: Silver는 그 칸의 현재 사진 정보를 비우고, 과거 버전 이력은 보존합니다. 같은 주소가 다시 생기면 같은 버전으로 `active`, 다른 주소면 새 버전입니다.
- **RDS 반영 [설계 결정, 11/22 차현지]**: Silver에서 `emptied`로 확정된 칸은 RDS `image_embedding`에서 해당 공고번호·사진 칸의 행을 삭제합니다. Silver 사진 버전 이력과 Gold 결과는 보존합니다. URL이 남아 있는 HTTP 404에는 이 삭제 규칙을 적용하지 않습니다. RDS 동기화 구현은 별도 작업입니다.
- 목록에서 빠진 공고(보호 종료 등)는 나오지 않습니다.

### 14.3 칸

| 칸 | 뜻 |
|---|---|
| `desertion_no`, `slot` | 키. `slot`은 1~8 |
| `slot_state` | `active` / `emptied` |
| `current_version` | 마지막으로 저장에 성공한 버전. 없으면 null |
| `original_s3_key`, `serving_s3_key`, `thumb_s3_key`, `sha256`, `ext`, `bytes`, `source_url`, `stored_at` | 현재 버전의 저장 정보. `current_version`이 null이면 모두 null |
| `latest_version`, `latest_status`, `latest_url` | 가장 큰 버전과 그 상태(`success`·`pending`·`failed`·`gave_up`), 주소. `emptied`면 `latest_url` null |
| `first_seen_at` | 가장 큰 버전의 주소를 처음 본 시각 |

### 14.4 `snapshot.json`

`source`, `photo_run_id`, `bronze_run_id`, `generated_at`, `code_version`, `status`(`ok`), `file`, `checksum`(gz 파일 sha256), 그리고 건수 `row_count`, `notice_count`, `stored_count`, `no_current_count`, `emptied_count`, `url_reverted_count`(주소가 옛 버전 주소로 돌아간 칸, 처리하지 않고 건수만).

### 14.5 검증

- **A (쓰기 전)**: (`desertion_no`, `slot`) 중복 없음, `slot` 1~8, `slot_state` 값, 현재 null이면 현재 칸 전부 null, `emptied`는 현재 사진 없음·`latest_url` null, `sha256` 64자, 원본 키가 버전 규칙(`popfile<N>[_vNNN].<ext>`)과 맞음, `current_version` ≤ `latest_version`, `row_count = stored_count + no_current_count + emptied_count`, `notice_count` 일치. 하나라도 어기면 쓰지 않고 사진 작업을 실패시킵니다.
- **B (이광훈 님)**: `status`, 체크섬, 키 중복, 위 건수 합, 공고 수, `slot` 범위, null 일관성, S3 저장 증거, 직전 스냅샷보다 버전이 줄지 않음. 갱신할 때는 기존 현재 사진 테이블과 스냅샷을 (`desertion_no`, `slot`)으로 비교합니다.

### 14.6 실패 처리

- 검증 실패: B는 기존 데이터를 유지하고 실행을 실패로 둡니다.
- 사진 작업은 성공했는데 스냅샷이 없음: 계약 위반으로 봅니다.
- manifest `aborted` + `abort_reason`이 `snapshot:`으로 시작: 사진은 받았지만 스냅샷이 없습니다. 기존 데이터를 유지하고 실행을 실패로 둡니다.
- 스냅샷은 있는데 manifest가 `in_progress`로 남음(마지막 `ok` 저장만 실패): 기존 정체 규칙(1시간)으로 처리합니다. 스냅샷은 그 실행 결과까지 반영한 것이라 틀린 값은 아닙니다.

### 14.7 실측 (11/20 상태 파일, 오프라인)

| 항목 | 값 |
|---|---|
| 줄 / 공고 | 41,891 / 18,809 |
| 현재 사진 있음 / 없음 | 38,836 / 3,055 (`gave_up` 2,303, `failed` 752) |
| `emptied` / 주소 되돌아감 | 0 / 0 |
| 크기 / 시간 | gz 2.5MB (원본 31MB) / 1.6초 |

### 14.8 범위 밖

- 빈(백지) 사진 측정값(`pixel_std` 등)은 나중에 칸을 더합니다. Bronze는 측정만, 판정은 Silver.
- 주소가 옛 버전으로 돌아간 칸의 처리 규칙(지금은 건수만).

## 15. 글 태그 — 성격·건강정보 (Gold, Jev 결과) [v1, test 환경, 2026-11-23]

보호중 개·고양이 공고의 글에 성격 태그(8개 중 0개 이상)와 건강정보 태그(정보 있음/없음 1개)를 붙여 S3 Gold 에 씁니다.
사진 태그(13절)와 같은 태깅 묶음 표 3개를 쓰고, 새 표는 만들지 않습니다. 코드는 `src/rebom/tagging/run_text_tags.py`(실행),
`text_tags.py`(질문지·태그·판정), `jev_client.py`(Decisions API 호출), 질문 문구는 `src/rebom/tagging/data/text_questions_v1.json`.
지금은 PC 에서 손으로 실행해 Gold test 에 씁니다. DAG·이미지는 다음 단계입니다.

### 15.1 입력과 대상

- 대상: Silver 공고 표에서 `status_group = PROTECTING`, `species_code` 개(417000)·고양이(422400). `--species` 로 바꿀 수 있습니다.
- 글: `info_text`(= `sfe_soci` + `sfe_health` + `special_mark`, Silver 변환이 이어 붙인 값). 비어 있으면 `special_mark`.
  sfe 칸이 있는 공고(보호중 약 2%)는 성격·건강 내용이 sfe 에만 있는 경우가 있어 `info_text` 를 씁니다(2026-11-23 Silver test 실측).
- 보내기 전 정리: A2 글 카드의 특징 글(`rebom.embedding.text_card.persona`)과 같은 규칙입니다(2026-11-23 결정). 앞 관리번호를 지우고, 공백을 한 칸으로 줄이고, 전화번호를 `[번호]`로 가립니다. 정규식(`LEADING_ID`·`PHONE`)도 `text_card` 것을 그대로 가져다 씁니다.
- 정리한 글이 비었거나 자리채움 값(`.`, `-`, `없음`, `해당없음`, `특이사항 없음` 등)이면 부르지 않고 `NO_TEXT` 로 기록합니다.
  "이상 없음"은 건강정보가 있는 글이라 자리채움 값이 아닙니다.
- `subject_type = animal`, `subject_key = 공고번호`, `input_hash` = 정리한 글의 sha256 (13.0 공용 키).

### 15.2 다시 태깅하는 조건

- 같은 (공고, 작업, 버전, `input_hash`) 가 `OK`·`NO_RESULT`·`NO_TEXT` 거나 `FAILED` 3번이면 건너뜁니다.
  그래서 글이 바뀌거나(`input_hash`) 질문 버전을 올릴 때만 다시 부릅니다.
- 같은 실행 안에서 글이 같은 공고들은 한 번만 부르고 결과를 나눠 씁니다(보호중 18,730건 중 서로 다른 글 약 12,500개, 11-23 실측).
- 키·계정 오류(401, 402, 키 한도)면 그 배치까지 저장하고 멈춥니다(종료 코드 2). 공고 실패로 세지 않습니다.

### 15.3 호출과 질문지 (v1)

- OpenRouter Decisions API(`POST /api/alpha/decisions`), 모델 `typesafe/jev-1.14`, 공급자 TypeSafe 고정(`allow_fallbacks: false`).
  키 `OPENROUTER_KEY_TAG_JEV`: PC 시험은 로컬 테스트 키, 서버는 k3s Secret `rebom-tag-jev-prod` 의 서버 키.
- 글 1건당 요청 1번에 질문 9개를 함께 보냅니다: 성격 예/아니오(noul) 8개 + 건강정보 선택형(choice) 1개.
  시험(402건)에서 입력 평균 3,919토큰, 1건당 약 $0.000165 였습니다(따로 보낼 때보다 약 7% 적음).
- 질문 문구는 jev_final_test 에서 시험한 질문을 글자 그대로 옮겼습니다. 이 레포의 버전 이름은 v1 입니다.

| 작업(`task`) | `prompt_version` | 옮겨 온 시험 질문 | 시험 결과 (태그 기준표) |
|---|---|---|---|
| `text_personality` | `text-personality-v1` | personality-v10 (= p12) | 새 표본 200건 완전 일치 0.905, 정밀도 0.989·재현율 0.944 |
| `text_health` | `text-health-v1` | health-info-s4c | 새 표본 200건 정확도 0.965, 정보 있음 정밀도 0.940·재현율 1.000 |

시험은 `special_mark` 로 했고, `info_text`(sfe 포함 글)로는 402건을 돌려 결과만 확인했습니다(정답 비교 전).
질문 문구를 바꾸면 JSON 을 새 파일(v2)로 만들고 버전을 올립니다. 버전이 바뀌면 모든 공고를 다시 태깅합니다.

### 15.4 태그와 판정 기준

| 태그 코드 | 이름 | 판정 | 검색 | 화면 |
|---|---|---|---|---|
| `personality.gentle` | 순함 | 예 확률 0.75 이상 | O | O |
| `personality.friendly` | 친화적 | 〃 ("경계심 없음"도 친화적) | O | O |
| `personality.timid` | 겁많음 | 〃 | O | O |
| `personality.wary` | 경계 | 〃 (겁 때문이라도 사람·손을 피하면 해당) | O | O |
| `personality.active` | 활발 | 〃 | O | O |
| `personality.aggressive` | 사나움 | 〃 (방어적이라고 적히지 않은 입질 포함) | O | O |
| `personality.sensitive` | 예민 | 〃 | O | O |
| `personality.curious` | 호기심 | 〃 | O | O |
| `health.info` | 건강정보 있음 | 정보 있음 확률이 더 높음 | O | O |
| `health.none` | 건강정보 없음 | 정보 없음 확률이 더 높음(건강하다는 뜻이 아님) | O | O |

- 건강정보는 정보 있음·정보 없음 두 값을 모두 라벨로 검색·화면에 넘깁니다(2026-11-23 결정). 글이 없어 부르지 않은 공고(`NO_TEXT`)는 태그 행이 없습니다.
- 성격 0.4~0.75 는 태그로 쓰지 않고 `detail.review` 에 남깁니다. 건강정보 1등 확률이 0.4 미만이면 `detail.review = true`.
- 한 글에 여러 성격 태그가 붙을 수 있습니다. 순함+사나움처럼 뜻이 반대여도 글에 둘 다 적혀 있으면 둘 다 붙이고 따로 표시하지 않습니다(2026-11-23 결정).
- 판정 근거: Jev 는 근거 구절을 돌려주지 않고 질문별 확률만 줍니다. 그래서 Jev 에 실제로 보낸 글(위 정리 규칙을 거친 글)을
  모든 글 태그 행의 `detail.input_text` 에 남깁니다(2026-11-23 결정). 태그 표에 칸을 더하지 않고 판정 근거 JSON(`detail`)에 둡니다.
- 해당·제외 문장의 근거는 "태그 기준표 — 성격 · 건강정보"(성격 1-3-4·1-3-10, 건강정보 2-0)입니다.

### 15.5 Gold 에 쓰는 방식

| 테이블 (test 는 `_test`) | 글 태그에서 쓰는 방식 |
|---|---|
| `gold_animal_tag` | `source = info_text`(태그를 만든 입력 칸. 사진 태그의 `photo` 와 같은 뜻. 2026-11-23 `jev` 에서 바꿈, 서비스 DB 로 옮길 때 값 대응 확인 필요). 성격은 태그마다 1행, 건강정보는 1행. `similarity` = 예 확률(성격)·1등 확률(건강) × 100, `source_hash` = `input_hash`, `tagger_version` = `typesafe/jev-1.14@<버전>`, `detail` = 확률 전체(`probs`)·`review`·`request_id`·`input_text`(Jev 에 보낸 가린 글). 교체 범위는 (`source`=info_text, animal, 공고번호, 작업, 버전, `source_hash`)입니다. 글이 바뀌거나 버전을 올리면 새 행이 더해지고 **이전 글·이전 버전 행은 남습니다**(13.3 이전 버전 보존과 같은 규칙, 2026-11-23). 같은 글을 다시 쓸 때(실패 재시도·끊긴 실행)만 그 글의 행을 교체해 중복을 막습니다. 사진 태그 행은 건드리지 않습니다. 글이 비게 되어도(`NO_TEXT`) 이전 행은 지우지 않습니다 |
| `gold_inference_log` | 공고 × 작업 × 버전 × `input_hash` 1행 upsert. `status` = `OK`(태그 있음)·`NO_RESULT`(성격 태그 없음)·`FAILED`·`NO_TEXT` |
| `gold_model_request_log` | Jev 요청 1번 = 1행, `task = text_jev`, `prompt_version = text-personality-v1+text-health-v1`. `subject_key` = 그 글을 쓰는 첫 공고, `raw_answer` = 응답 `answers` 전체(원 확률), `cost_usd` = `usage.cost`. 같은 글을 쓰는 다른 공고는 태그 `detail.request_id` 로 잇습니다 |

- `version` 칸(13.6 버전 레지스트리): 글 태그는 아직 등록 버전(`task@N`)을 쓰지 않아 태그 표·요청 기록 표의 `version` 칸을 비워 둡니다. 13.6의 "기존 결과의 `version` 은 null 로 두고 기존 질문지 키로 계속 읽는다"와 같은 방식이며, 처리 기록의 키 `version` 에는 지금처럼 질문지 버전(`text-personality-v1`, `text-health-v1`)이 들어갑니다. 등록 버전 연결은 15.7 의 후속 작업입니다(2026-11-24).
- 실행 중간 파일(대상 목록): `gold/_work_test/text_tags/<run_id>/targets.json`. 목록에는 정리한 글(전화번호 가림)이 들어 있습니다.
- 서비스 DB 에 넣을 활성 태그(현재 태그): 아래를 모두 만족하는 행만 고릅니다. 이전 글·이전 버전 행이 남아 있으므로 이 조건이 꼭 필요합니다.
  1. 공고 `status_group` 이 보호중
  2. `prompt_version` 이 작업별 현재 버전(`text_tags.VERSIONS`: `text-personality-v1`, `text-health-v1`)
  3. `source_hash` 가 그 공고·작업·버전의 처리 기록 중 `gold_updated_at` 이 가장 늦은 행의 `input_hash` 이고, 그 처리 기록 `status` 가 `OK`·`NO_RESULT`
     (가장 늦은 기록이 `NO_TEXT` 면 현재 글에 내용이 없다는 뜻이라 활성 태그가 없습니다)

### 15.6 실행 (PC, 레포 루트)

```
python -m rebom.tagging.run_text_tags select --profile <프로필> --limit 50 --targets data/tag_text/targets.json --dry-run
python -m rebom.tagging.run_text_tags select --profile <프로필> --limit 50 --targets data/tag_text/targets.json
python -m rebom.tagging.run_text_tags run --profile <프로필> --targets data/tag_text/targets.json --local-out data/tag_text/out
python -m rebom.tagging.run_text_tags run --profile <프로필> --targets data/tag_text/targets.json      (Gold test 에 쓰기)
```

- `--local-out` 이 있으면 Gold 대신 로컬 JSONL 에 씁니다. 빼면 Gold test 표에 씁니다(표는 13.3 대로 이미 있음).
- 보호중 전체는 요청 약 1.25만 번, 약 $2 입니다(추정). 동시 8개면 약 10분. 전체 적재는 `--batch-size 2000` 이상으로 돌려 저장 횟수(작은 파일)를 줄입니다(기본 200 이면 60번 넘게 나뉨).
- 2026-11-23 시험 적재: Silver test 보호중 개·고양이 200건 → 요청 159번(같은 글 41건), 태그 384행, 처리 기록 1,538행(NO_TEXT 569 × 2 포함), 실패 0, 비용 $0.026.

### 15.7 남은 일

- Gold 버전 레지스트리 연결: `text_personality@1`·`text_health@1` 을 13.6 대로 등록하고, 태그·처리 기록·요청 기록에 등록 버전을 남기기(사진 태깅과 같은 방식).
- DAG(`dags/text_tags.py`)와 실행 이미지: 사진 태깅처럼 Silver apply 전환 뒤 연결. 이미지·ECR·네임스페이스·Secret 은 사람이 만듭니다.
- 같은 글 재사용을 실행 사이로 넓히기(지금은 한 실행 안에서만).
- 기준 확인: sfe 의 "양호"(칸 이름 없이 붙어 정보 없음으로 판정), "밝은 성격"(활발 여부).
