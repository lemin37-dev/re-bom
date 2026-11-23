/**
 * 품종 정보(크기): 품종의 대표 몸무게 범위. 공고 상세 '외형 정보'에서 공고 몸무게·크기 아래에 보여 줍니다.
 *
 * - 출처 우선순위: FCI 품종 표준 → 국가유산청(천연기념물 설명) → Dog API → 빈칸. 미국애견협회(AKC) 자료는 쓰지 않습니다.
 *   몸무게는 FCI와 Dog API 범위를 합칩니다(작은 하한 ~ 큰 상한). 키는 화면에 쓰지 않습니다.
 * - 원자료: Drive rebom_out/dataset_v2/breed_features_v1.csv, fci_cross_check.csv (11-06),
 *   11-23 다시 확인: FCI 표준 PDF 원문, Dog API /v1/breeds. 근거는 docs/breed_size_table_20261123.md
 * - 크기가 여러 가지인 품종(푸들·슈나우저·닥스훈트)은 줄을 나눕니다. 공고 품종명에 크기가 있으면 그 줄만,
 *   계열 이름(예: '푸들')이면 모든 줄을 보여 줍니다(lib/breed-size.ts).
 * - 이 표에 없는 품종·믹스견은 보여 주지 않습니다.
 */

export type BreedSizeSource = 'FCI' | '국가유산청' | 'Dog API' | '팀 결정'

export type BreedSizeLine = {
  /** 화면에 쓰는 이름 (공고 품종명 표기) */
  name: string
  /** 이 줄에 해당하는 공고 품종명(kindNm). 비교할 때 띄어쓰기·'져/저'·'쳐/처'는 무시 */
  kindNm: string[]
  /** kg. null 이면 그쪽 끝 자료 없음 (minKg 만 null 이면 'NNkg 이하') */
  minKg: number | null
  maxKg: number | null
  sources: BreedSizeSource[]
}

export type BreedSizeFamily = {
  family: string
  /** 계열 이름만 적힌 공고 품종명. 이 이름이면 모든 줄을 보여 줌 */
  familyKindNm: string[]
  lines: BreedSizeLine[]
}

const one = (name: string, minKg: number, maxKg: number, sources: BreedSizeSource[], aliases: string[] = []): BreedSizeFamily => ({
  family: name,
  familyKindNm: [],
  lines: [{ name, kindNm: [name, ...aliases], minKg, maxKg, sources }],
})

export const BREED_SIZES: BreedSizeFamily[] = [
  {
    family: '푸들',
    familyKindNm: ['푸들'],
    lines: [
      // FCI 172 는 4가지 크기를 키로만 정함(몸무게 없음) → 몸무게는 Dog API
      { name: '토이 푸들', kindNm: ['토이 푸들'], minKg: 1.8, maxKg: 2.7, sources: ['Dog API'] },
      { name: '미니어쳐 푸들', kindNm: ['미니어쳐 푸들'], minKg: 4.5, maxKg: 6.8, sources: ['Dog API'] },
      // Dog API 에 미디엄이 없음. 미니어쳐 상한과 스탠다드 하한 사이 값 (11-23 팀 결정, 위키백과 등 대부분 이 값)
      { name: '미디엄 푸들', kindNm: ['미디엄 푸들'], minKg: 6.9, maxKg: 17.9, sources: ['팀 결정'] },
      { name: '스탠다드 푸들', kindNm: ['스탠다드 푸들'], minKg: 18, maxKg: 32, sources: ['Dog API'] },
    ],
  },
  {
    family: '슈나우져',
    familyKindNm: ['슈나우져'],
    lines: [
      { name: '미니어쳐 슈나우져', kindNm: ['미니어쳐 슈나우져'], minKg: 4, maxKg: 9, sources: ['FCI', 'Dog API'] }, // FCI 183 4~8, Dog API 5~9
      { name: '스탠다드 슈나우져', kindNm: ['스탠다드 슈나우져'], minKg: 14, maxKg: 23, sources: ['FCI', 'Dog API'] }, // FCI 182 14~20, Dog API 14~23
      { name: '자이언트 슈나우져', kindNm: ['자이언트 슈나우져'], minKg: 25, maxKg: 47, sources: ['FCI', 'Dog API'] }, // FCI 181 35~47, Dog API 25~39
    ],
  },
  {
    family: '닥스훈트',
    familyKindNm: ['닥스훈트'],
    lines: [
      // FCI 148 은 가슴둘레로만 나눔(몸무게 없음) → 몸무게는 Dog API
      { name: '스탠다드 닥스훈트', kindNm: ['스탠다드 닥스훈트'], minKg: 7, maxKg: 14.5, sources: ['Dog API'] },
      { name: '미니어쳐 닥스훈트', kindNm: ['미니어쳐 닥스훈트'], minKg: null, maxKg: 5, sources: ['Dog API'] }, // 'up to 5'
      { name: '래빗 닥스훈트', kindNm: ['래빗 닥스훈트'], minKg: null, maxKg: null, sources: [] }, // 몸무게 자료 없음
    ],
  },
  one('진도견', 14, 23, ['FCI', 'Dog API']), // FCI 334 15~23
  one('말티즈', 1.8, 4, ['FCI', 'Dog API']), // FCI 3~4, Dog API 1.8~3.2
  one('포메라니안', 1.4, 3.2, ['Dog API']), // FCI 97(저먼 스피츠)은 크기별 몸무게 없음
  one('비숑 프리제', 5, 8, ['FCI', 'Dog API']), // FCI 215 '약 5kg', Dog API 5~8
  one('라브라도 리트리버', 25, 36, ['Dog API']),
  one('시바', 7, 11, ['Dog API']),
  one('치와와', 1, 3, ['FCI', 'Dog API']),
  one('스피츠', 4.5, 11, ['Dog API']), // FCI 262 재패니즈 스피츠로 연결, Dog API Japanese Spitz (저먼 스피츠 3.6~12 아님)
  one('프렌치 불독', 7, 14, ['FCI', 'Dog API']), // FCI 101 8~14, Dog API 7~13
  one('보더 콜리', 14, 25, ['Dog API']),
  one('골든 리트리버', 25, 34, ['Dog API']),
  one('시베리안 허스키', 15.5, 28, ['FCI', 'Dog API']),
  one('웰시 코기 펨브로크', 9, 14, ['FCI', 'Dog API']),
  one('웰시 코기 카디건', 11, 17, ['Dog API']),
  one('시츄', 4, 8, ['FCI', 'Dog API']),
  one('요크셔 테리어', 1.8, 3.2, ['FCI', 'Dog API']),
  one('미니어쳐 핀셔', 3.5, 6, ['FCI', 'Dog API']),
  one('라이카', 16, 25, ['Dog API']), // 웨스트 시베리안 라이카
  one('사모예드', 16, 29, ['Dog API']),
  one('삽살개', 14, 28, ['Dog API']), // FCI 목록에 없음. 국가유산청 설명에는 키만 있음
  one('풍산견', 20, 36, ['Dog API']), // FCI·국가유산청(남한 천연기념물) 목록에 없음
  one('동경견', 14, 18, ['Dog API']), // 국가유산청 설명에는 몸무게 없음
  one('노퍽 테리어', 5, 5.5, ['Dog API']),
  one('포인터', 20, 34, ['Dog API'], ['잉글리쉬 포인터']),
  one('셰퍼드', 22, 41, ['FCI', 'Dog API'], ['저먼 셰퍼드 독']),
  one('베들링턴 테리어', 8, 10, ['FCI', 'Dog API']),
  one('비글', 9, 14, ['Dog API']),
  one('마리노이즈', 18, 36, ['FCI', 'Dog API']), // FCI 15 20~30, Dog API Belgian Malinois 18~36
  one('차우차우', 20, 32, ['Dog API']),
  one('코카 스파니엘', 9, 14, ['Dog API'], ['아메리칸 코카 스파니엘']), // FCI 167 아메리칸으로 연결 (잉글리시 12~15.5 아님)
  one('보스턴 테리어', 5, 11.35, ['FCI', 'Dog API']), // FCI 140 6.8~11.35
  one('퍼그', 6, 8.1, ['FCI', 'Dog API']), // FCI 253 '6,3 – 8,1 kg', Dog API 6~8
]
