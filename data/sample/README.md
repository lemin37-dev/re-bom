# 구조동물 샘플 데이터

구조동물 공공 API(data.go.kr 15098931 `abandonmentPublicService_v2`) 응답에서 뽑은 샘플입니다. 2026-10-30 기준이고, 응답 필드는 가공하지 않았습니다. 필드 뜻은 [docs/rescue_fields.md](../../docs/rescue_fields.md)를 보세요.

## 파일

| 파일 | 내용 |
| --- | --- |
| `rescue_sample.json` | 공고 200건(보호중 100 + 종료 100). 개 91 · 고양이 97 · 기타 12. 사진 3장 이상, 특징 문장 9자 이하, 체중 형식이 섞인 것, 건강·접종 체크가 있는 것을 일부러 포함 |
| `incremental/` | 증분 수집 시험용. 같은 공고 115건을 두 시점(10/28, 10/30)으로 나눈 쌍과 API 조회 결과 |
| `aggregates/rescue_counts.csv` | 월별 건수(2008-01 ~ 2026-10, 합계 1,649,168건) |
| `aggregates/photo_check_by_year.csv` | 연도별 사진 열림 비율·평균 용량(연도당 약 100건 표본) |

### `incremental/`

| 파일 | 뜻 |
| --- | --- |
| `t0_2026-10-28.json` | 이전 수집본. 이 시점에 없던 신규 공고는 빠져 있음 |
| `truth_2026-10-30.json` | 정답. 같은 날 발생일 기준으로 전부 다시 받은 결과 |
| `api_updated.json` | 수정일 증분(`bgupd=20261028`)이 돌려준 것 |
| `api_list_notice.json`, `api_list_protect.json` | 보호중 목록(`state=notice`, `state=protect`)이 돌려준 것 |
| `api_lookup.json` | 보호중 목록에서 사라진 공고를 따로 조회한 결과 |
| `expected.json` | 공고번호를 유형별로 나눈 것(복귀, 상태 변경인데 수정일 조회가 놓침, 사진·비고만 변경 등) |

확인 방법(API 호출 없음):

```bash
python scripts/rescue/check_incremental.py
```

이전 수집본에 ① 수정일 증분 ② 보호중 목록 ③ 사라진 공고 조회를 차례로 합쳐 정답과 비교합니다. 이 샘플에서의 결과입니다.

| 방식 | 공고번호 차이 | 상태 차이 | 내용이 다른 공고 |
| --- | --- | --- | --- |
| ① 수정일 증분만 | 3 | 28 | 43 (상태 변경인데 놓침 25 · 사진·비고만 변경 15 · 복귀 3) |
| ① + ② + ③ | 0 | 0 | 7 (모두 종료 공고의 사진·비고 칸) |

전체 데이터(29,463건)로 한 시험 결과는 노션 "구조 API 검증 결과" 2.7절에 있습니다. 여기 샘플은 유형별로 골라 담은 것이라 비율이 아니라 방식이 맞는지 확인하는 용도입니다.

## 개인정보 처리

공개 저장소라서 원본에서 아래를 처리한 사본입니다.

- `careOwnerNm`(보호소 대표자 이름): 삭제
- `careAddr`: 시·도, 시·군·구까지만 남김
- 휴대전화 모양 번호: 뒤 4자리를 `****`로 가림(보호소 일반 전화는 기관 연락처라 그대로)

`python scripts/rescue/check_pii.py`로 샘플에 휴대전화·주민번호 모양·이메일·`careOwnerNm`이 남았는지 검사합니다. 사진 파일은 올리지 않았고 주소(URL)만 있습니다. 주소는 `http://`이고 2021-11 이전 공고의 사진은 열리지 않습니다.

## 다시 만들기

인증키가 필요합니다. `.env`에 `DATA_GO_KR_SERVICE_KEY`를 넣고(키는 저장소에 올리지 않음), `pip install requests python-dotenv`가 필요합니다.

```bash
python scripts/rescue/fetch_snapshot.py snap.json --since 20261028 --from 20260701   # 읽기 전용, 호출 약 50번
python scripts/rescue/make_samples.py --t0 이전_수집본.json --snapshot snap.json
python scripts/rescue/rescue_count.py            # 월별 건수 (호출 약 220번)
python scripts/rescue/photo_check_by_year.py     # 연도별 사진 표본
```

`make_samples.py`는 이전 수집본(JSON 배열)이 필요합니다. 이 샘플의 이전 수집본은 2026-07-01 ~ 10-27 발생 29,463건(10/28 저장)입니다. 일 호출 한도는 10,000건입니다.
