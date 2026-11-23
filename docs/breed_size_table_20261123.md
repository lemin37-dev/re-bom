# 품종 정보(크기) 표 근거 (2026-11-23)

공고 상세 '외형 정보'의 **품종 정보(크기)** 값과 그 근거입니다. 화면 데이터는 `frontend/src/data/breed-size.ts`, 조회·표기는 `frontend/src/lib/breed-size.ts`입니다.

## 1. 정한 것 (11-23)

- **몸무게와 크기만** 보여 줍니다. 키는 쓰지 않습니다(퍼그처럼 키를 알 수 없는 품종도 있음).
- 크기는 몸무게 범위의 양 끝을 Silver와 같은 경계(10kg 미만 소형 · 25kg 미만 중형 · 이상 대형)로 나눠 표기합니다. 양 끝이 다르면 **범위**로 씁니다. 예: 시바 `7~11kg, 소형~중형`.
  - 공고 개체 하나는 크기 하나(Silver `size`)를 가지지만, 품종으로 묶으면 실제 공고도 범위에 걸치는 경우가 많아서입니다.
- 크기가 여러 가지인 품종(푸들·슈나우저·닥스훈트)은 줄을 나눕니다.
  - 공고 품종명에 크기가 있으면(`토이 푸들`, `미니어쳐 닥스훈트`) **그 한 줄만** 보여 줍니다.
  - 계열 이름만 있으면(`푸들`, `슈나우져`) **모든 줄**을 보여 줍니다.
- **3년 치 공고에 한 번이라도 나온 품종은 모두** 넣습니다(`src/rebom/tagging/data/breed_candidates_3y.json`의 62종, 공고 품종명 표기 71가지). 몸무게 자료가 없는 품종(래빗 닥스훈트, 미텔 스피츠)과 믹스견은 보여 주지 않습니다.
- 출처 우선순위: **FCI 품종 표준 → 국가유산청 → Dog API → 빈칸**. 미국애견협회(AKC) 자료는 쓰지 않습니다. 몸무게는 FCI와 Dog API 범위를 합칩니다(작은 하한 ~ 큰 상한).

화면 예 (공고 품종명 `푸들`, 공고 체중 11kg):
```
11kg · 중형                         ← 이 아이 (Silver size)
품종 정보(크기)
  토이 푸들      1.8~2.7kg, 소형
  미니어쳐 푸들  4.5~6.8kg, 소형
  미디엄 푸들    6.9~17.9kg, 소형~중형
  스탠다드 푸들  18~32kg, 중형~대형
품종의 대표 몸무게예요. 이 아이의 크기는 위 공고 체중 기준이에요 · 출처 Dog API · 팀 결정
```

## 2. 자료를 받은 방법 (Drive `rebom_out/dataset_v2/`, 11-06)

| 출처 | 받는 방법 | 결과 파일 | 11-23 다시 확인 |
|---|---|---|---|
| FCI | ① 품종 목록: GitHub `paiv/fci-breeds` CSV(MIT, 원 출처 fci.be 품종 목록) ② 우리 품종명 → FCI 번호 손으로 대응(32종, '확인' 메모 8종) ③ 표준 PDF 보관 ④ pypdf로 구절 추출 | `fci/fci-breeds.csv`, `fci/NNN.pdf`, `fci_cross_check.csv` | ✅ 라이선스 MIT, CSV 크기 같음. **PDF 주소는 `fci.be/Nomenclature/Standards/NNNg<그룹 2자리>-en.pdf`**(예: 푸들 `172g09`, `172g01`은 404) |
| 국가유산청 | '국가유산 검색' 공개 API(키 없음). 목록 `SearchKindOpenapiList.do` → 종목코드 16(천연기념물) → 상세 `SearchKindOpenapiDt.do` | `korean_breed_heritage.csv` | ✅ 상세 API로 진도의 진도개(1962)·경산의 삽살개(1992)·경주개 동경이(2012) 응답. **풍산개는 없음**(남한 천연기념물 아님) |
| Dog API | `api.thedogapi.com/v1/breeds`, 키 필요(없으면 403). 키는 Secrets Manager `rebom/thedogapi` | `dogapi_breeds.json` | ✅ 631품종. Sapsali·Pungsan·Donggyeongi 포함 |
| 통합 | 우선순위대로 칸마다 출처 기록, 몸무게는 FCI와 Dog API 합침 | `breed_features_v1.csv` | 아래 3절 값 고침 |

## 3. `breed_features_v1.csv`에서 고친 값 (원문 확인)

| 품종 | 이전 | 고친 값 | 근거 |
|---|---|---|---|
| 푸들 | 18~32kg 한 줄 | 4줄 (1절 예) | FCI 172: 4가지 크기를 키로만 정함(Standard 45~60cm, Medium 35~45, Miniature 28~35, Toy 24~28). 몸무게는 Dog API Poodle(Toy) 1.8~2.7 · Poodle(Miniature) 4.5~6.8 · Poodle 18~32. 미디엄은 Dog API에 없어 6.9~17.9(미니어쳐 상한과 스탠다드 하한 사이, 팀 결정) |
| 슈나우저 | 4~23kg | 3줄: 미니어쳐 4~9 · 스탠다드 14~23 · 자이언트 25~47 | FCI 183 "approximately 4 to 8 kg", FCI 182 "14 to 20 kg", FCI 181 "35 to 47 kg". Dog API Miniature 5~9, Standard 14~23, Giant 25~39 |
| 닥스훈트 | 7~14.5kg 한 줄 | 3줄: 스탠다드 7~14.5 · 미니어쳐 5kg 이하 · 래빗 자료 없음 | FCI 148은 가슴둘레로만 나눔(몸무게 없음). Dog API Dachshund 7~14.5, Miniature Dachshund "up to 5". 래빗은 FCI·Dog API 모두 몸무게 없음 |
| 비숑 프리제 | 5~5kg | 5~8kg | FCI 215 "Approximately 5 kg", Dog API 5~8 (합치기 규칙이 빠졌던 값) |
| 퍼그 | 3~8.1kg | 6~8.1kg | FCI 253 "Ideal weight 6,3 – 8,1 kg"(PDF 글자 "6 ,3"을 3으로 읽음), Dog API 6~8 |
| 스피츠 | 3.6~12kg | 4.5~11kg | FCI는 재패니즈 스피츠(262)로 연결했는데 몸무게는 Dog API 저먼 스피츠 값이었음. Dog API Japanese Spitz 4.5~11 |
| 코카 스파니엘 | 9~15.5kg | 9~14kg | FCI는 아메리칸(167)으로 연결. 15.5는 잉글리시 코카 값. Dog API American Cocker Spaniel 9~14 |

키 추출 오류(사모예드 3~57cm, 프렌치 불독 1~35cm, 마리노이즈 12.5~62cm, 국가유산청 키 빈칸)는 키를 화면에 쓰지 않아 표에서 뺐습니다. 원문 값: 사모예드 50~60cm(57±3, 53±3), 프렌치 불독 24~35cm, 마리노이즈 56~66cm(62/58, −2·+4).

## 4. 표 (61계열, 68줄)

공고 5건 이상 품종 (11-06 자료를 고침):

| 품종 | 몸무게 (kg) | 크기 | 출처 |
|---|---|---|---|
| 푸들 (토이 · 미니어쳐 · 미디엄 · 스탠다드) | 1.8~2.7 · 4.5~6.8 · 6.9~17.9 · 18~32 | 소형 · 소형 · 소형~중형 · 중형~대형 | Dog API, 미디엄은 팀 결정 |
| 슈나우져 (미니어쳐 · 스탠다드 · 자이언트) | 4~9 · 14~23 · 25~47 | 소형 · 중형 · 대형 | FCI, Dog API |
| 닥스훈트 (스탠다드 · 미니어쳐 · 래빗) | 7~14.5 · 5 이하 · 자료 없음 | 소형~중형 · 소형 · — | Dog API |
| 진도견 | 14~23 | 중형 | FCI 334, Dog API |
| 말티즈 | 1.8~4 | 소형 | FCI, Dog API |
| 포메라니안 | 1.4~3.2 | 소형 | Dog API |
| 비숑 프리제 | 5~8 | 소형 | FCI, Dog API |
| 라브라도 리트리버 | 25~36 | 대형 | Dog API |
| 시바 | 7~11 | 소형~중형 | Dog API |
| 치와와 | 1~3 | 소형 | FCI, Dog API |
| 스피츠 | 4.5~11 | 소형~중형 | Dog API |
| 프렌치 불독 | 7~14 | 소형~중형 | FCI, Dog API |
| 보더 콜리 | 14~25 | 중형~대형 | Dog API |
| 골든 리트리버 | 25~34 | 대형 | Dog API |
| 시베리안 허스키 | 15.5~28 | 중형~대형 | FCI, Dog API |
| 웰시 코기 펨브로크 | 9~14 | 소형~중형 | FCI, Dog API |
| 웰시 코기 카디건 | 11~17 | 중형 | Dog API |
| 시츄 | 4~8 | 소형 | FCI, Dog API |
| 요크셔 테리어 | 1.8~3.2 | 소형 | FCI, Dog API |
| 미니어쳐 핀셔 | 3.5~6 | 소형 | FCI, Dog API |
| 라이카 | 16~25 | 중형~대형 | Dog API |
| 사모예드 | 16~29 | 중형~대형 | Dog API |
| 삽살개 | 14~28 | 중형~대형 | Dog API |
| 풍산견 | 20~36 | 중형~대형 | Dog API |
| 동경견 | 14~18 | 중형 | Dog API |
| 노퍽 테리어 | 5~5.5 | 소형 | Dog API |
| 포인터 | 20~34 | 중형~대형 | Dog API |
| 셰퍼드 | 22~41 | 중형~대형 | FCI, Dog API |
| 베들링턴 테리어 | 8~10 | 소형~중형 | FCI, Dog API |
| 비글 | 9~14 | 소형~중형 | Dog API |
| 마리노이즈 | 18~36 | 중형~대형 | FCI 15, Dog API |
| 차우차우 | 20~32 | 중형~대형 | Dog API |
| 코카 스파니엘 | 9~14 | 소형~중형 | Dog API |
| 보스턴 테리어 | 5~11.35 | 소형~중형 | FCI, Dog API |
| 퍼그 | 6~8.1 | 소형 | FCI, Dog API |

공고 1~5건 품종 (11-23 추가, FCI 표준 PDF 원문 + Dog API):

| 품종 | 몸무게 (kg) | 크기 | 출처 · 근거 |
|---|---|---|---|
| 이탈리안 그레이 하운드 | 3~6.5 | 소형 | FCI 200 "maximum 5 kg", Dog API 3~6.5 |
| 브리타니 스파니엘 | 14~18 | 중형 | Dog API (FCI 95 키만) |
| 잭 러셀 테리어 | 5~8 | 소형 | FCI 345 "25 cms … 5 kgs, 30 cms … 6 kgs", Dog API 6~8 |
| 말라뮤트 | 32~41 | 대형 | FCI 243 수컷 38kg·암컷 34kg, Dog API 32~41 |
| 빠삐용(콘티넨탈 토이 스파니엘) | 1.5~5 | 소형 | FCI 77 "Minimum weight 1,5 kg", 암컷 최대 5kg, Dog API Papillon 2~4.5 |
| 샤페이 | 20~27 | 중형~대형 | Dog API (FCI 309 키만) |
| 노리치 테리어 | 5~5.5 | 소형 | Dog API (FCI 72 키만) |
| 휘펫 | 11~18 | 중형 | Dog API (FCI 162 키만) |
| 그레이트 피레니즈 | 39~73 | 대형 | Dog API Pyrenean Mountain Dog (FCI 137 키만) |
| 핏불테리어 | 14~27 | 중형~대형 | Dog API American Pit Bull Terrier (FCI 인정 품종 아님) |
| 페키니즈 | 3.2~6.4 | 소형 | FCI 207 "not exceeding 5 kg for males and 5.4 females", Dog API 3.2~6.4 |
| 그레이 하운드 | 27~32 | 대형 | Dog API (FCI 158 키만) |
| 오스트레일리안 켈피 | 12~21 | 중형 | Dog API (FCI 293 키만) |
| 저먼 쇼트헤어드 포인터 | 20~32 | 중형~대형 | Dog API (FCI 119 키만) |
| 도베르만 | 27~45 | 대형 | FCI 143 수컷 40~45·암컷 32~35kg, Dog API 27~45 |
| 그레이트 덴 | 50~79 | 대형 | Dog API (FCI 235 키만) |
| 벨지안 셰퍼드 독(테르뷰렌) | 20~34 | 중형~대형 | FCI 15 수컷 25~30·암컷 20~25kg, Dog API Belgian Tervuren 20~34 |
| 비즐라 | 20~27 | 중형~대형 | Dog API (FCI 57 키만) |
| 불독 | 18~25 | 중형~대형 | FCI 149 수컷 25kg·암컷 23kg, Dog API 18~25 |
| 미텔 스피츠 | 자료 없음 | — | FCI 97 "weight corresponding to its size"(키 35±5cm), Dog API는 저먼 스피츠 전체(3.6~12)뿐 → 숨김 |
| 세인트 버나드 | 54~82 | 대형 | Dog API (FCI 61 키만) |
| 코카시안 오브차카(코카시안 셰퍼드 독) | 45~77 | 대형 | FCI 328 최소 수컷 50·암컷 45kg, Dog API 45~77 |
| 도사 | 36~91 | 대형 | Dog API Tosa (FCI 260 키만) |
| 실키테리어 | 3.5~5 | 소형 | Dog API Australian Silky Terrier (FCI 236 "in proportion to height") |
| 달마시안 | 20~32 | 중형~대형 | Dog API (FCI 153 키만) |
| 아메리칸불리 | 25~54 | 대형 | Dog API (FCI 인정 품종 아님) |

- FCI 번호·PDF 주소는 `paiv/fci-breeds`의 `fci-breeds.csv`(`pdf` 칸)에서 찾았습니다.
- 아메리칸 코카 스파니엘·잉글리쉬 포인터·저먼 셰퍼드 독·저먼 포인터·빠삐용은 다른 품종명과 같은 줄로 봅니다.

## 5. 범위 밖 (손대지 않음)

- Silver 크기 경계(`<= 10` → `< 10`)와 전체 데이터의 크기 다시 만들기는 **Silver 담당이 작업합니다**(11-23). 프론트·이 표는 Silver가 고친 값을 받아 씁니다. 개인 브랜치의 예전 커밋(`c5467df`)에 있는 Silver 쪽 변경(`storage/cleaning/weight.py`, `storage/silver/transform_notice.py`)은 develop PR에 넣지 않습니다.
- 남은 일: 3년 치 보호소의 다 자란 개 몸무게와 품종별 범위 비교(원래 계획 E15·E17·E18), 래빗 닥스훈트·미텔 스피츠 몸무게 출처.
