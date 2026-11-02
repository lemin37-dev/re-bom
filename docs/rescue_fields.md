# 구조동물 API 요청 조건·응답 필드

공공데이터포털 Swagger 명세(상세기능 탭)를 읽고 호출로 대조한 내용입니다(2026-10-30). 기준: 수집본 30,139건.
호출 주소: `https://apis.data.go.kr/1543061/abandonmentPublicService_v2/abandonmentPublic_v2`, 일 호출 한도 10,000건.

## 요청 조건

| 조건 | 뜻 | 참고 |
| --- | --- | --- |
| `bgnde` / `endde` | 구조날짜(접수일) 범위, `YYYYMMDD` | 응답의 `happenDt` 기준. 기간 조건이 없으면 최근 약 1개월만 나옴 |
| `bgupd` / `enupd` | 수정일 범위, `YYYYMMDD` | 응답의 `updTm` 기준. 기존 공고의 상태 변경 일부와 사진 칸 추가는 돌려주지 않음 |
| `state` | 명세: `notice`(공고중), `protect`(보호중), 빈값(전체) | `finish`도 호출은 되지만 명세에 없는 비공식 동작. `notice`=보호중+공고 기간 안, `protect`=보호중+기간 지남 |
| `upkind` | 축종 코드: 개 417000, 고양이 422400, 기타 429900 | |
| `kind`, `upr_cd`, `org_cd` | 품종·시도·시군구 코드 | 각각 조회 API의 코드 |
| `care_reg_no` | 보호소번호 | 발생일과 함께 쓰면 좁혀짐 |
| `sex_cd`, `neuter_yn`, `rfid_cd` | 성별(M/F/Q), 중성화(Y/N/U), 동물등록번호 | |
| `desertion_no`, `notice_no` | 유기번호, 공고번호 | 공고 한 건만 나옴. 응답 필드 이름(`desertionNo`)으로 보내면 무시됨 |
| `numOfRows`, `pageNo` | 페이지당 건수(1,000 이하, 기본 10), 페이지 번호 | |
| `_type` | `xml`(기본) 또는 `json` | |

## 응답 필드

| 구분 | 필드 | 뜻 |
| --- | --- | --- |
| 식별 | `desertionNo` | 구조번호. 고유키 |
|  | `noticeNo` | 공고번호 문자열 |
| 날짜 | `happenDt` | 접수일(`20260909`) |
|  | `noticeSdt` / `noticeEdt` | 공고 시작일 / 종료일. 대부분 10–13일. 입양·기증·방사는 공고 기간이 끝난 뒤에만 나옴 |
|  | `updTm` | 수정일(`2026-10-28 15:54:54.0`). 시간대는 명세에 없음 |
| 동물 | `upKindCd` / `upKindNm` | 축종 코드 / 이름 |
|  | `kindCd` / `kindNm` / `kindFullNm` | 품종. 기타 축종은 `kindFullNm`에만 실제 이름 |
|  | `colorCd` | 색상(자유 기재) |
|  | `age` | 출생연도(`2026(년생)`) |
|  | `weight` | 체중(`13(Kg)`, `0.8~0.9(Kg)`, `330g(Kg)`처럼 형식이 섞임) |
|  | `sexCd` | 성별 `M`/`F`/`Q` |
|  | `neuterYn` | 중성화 `Y`/`N`/`U` |
|  | `specialMark` | 특징 문장. 98% 채워짐, 28%가 9자 이하 |
| 상태 | `processState` | `보호중`, `종료(입양)`, `종료(반환)`, `종료(자연사)`, `종료(안락사)`, `종료(기증)`, `종료(방사)` |
|  | `endReason` | 처분사유(자유 문장). 종료 공고의 28%에만 값이 있음 |
| 사진 | `popfile1` – `popfile8` | 사진 주소(`http://`). 3번 이후는 값이 있을 때만 옴. 공고당 평균 2.29장 |
| 장소 | `happenPlace`, `orgNm` | 발견 장소, 관할 시도·시군구 |
| 보호소 | `careRegNo`, `careNm`, `careTel`, `careAddr`, `careOwnerNm` | 등록번호, 이름, 전화, 주소, 대표자. 이 저장소의 샘플에서는 `careOwnerNm` 삭제, `careAddr`는 시군구까지 |
| 건강·성격 | `sfeSoci` / `sfeHealth` | 특징(사회성) / 특징(건강). 각 1.5%만 채워짐 |
|  | `healthChk` / `vaccinationChk` | 건강·접종 체크박스 문자열. 9.9% / 11.5% |
|  | `etcBigo`, `rfidCd` | 특이사항(내용 0건), 동물등록번호(5.7%) |
| 안내문 | `adptn*`, `sprt*`, `srvc*`, `evnt*` | 입양절차·입양지원·봉사·행사 안내. `adptn*`·`srvc*`는 0.5%, `sprt*`·`evnt*`는 0건 |

- 값이 비면 키 자체가 오지 않는 필드가 있어 공고마다 필드 개수가 다릅니다.
- 응답에 `desertionNo`가 없는 빈 항목이 섞여 올 수 있어 받을 때 걸러야 합니다(`real_items`).
- 요청 이름은 소문자와 밑줄, 응답 이름은 camelCase라서 서로 다릅니다.
