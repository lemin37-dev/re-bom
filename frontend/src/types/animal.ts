/** 매칭 이유 한 줄: [표시 문구, 근거 출처('문장' | '사진' | '조건')] */
export type MatchReason = [label: string, source: string]

export type Species = 'dog' | 'cat' | 'etc'

/** 공고의 보호소 정보 (검색 API `shelter`) */
export type AnimalShelter = {
  regNo: string | null
  name: string
  tel: string | null
  /** 시·군·구까지만 (샘플은 상세 주소를 지움) */
  addr: string | null
  sido: string | null
  sigungu: string | null
}

/** 보호센터 목록 한 줄 (검색 API `/shelters`) */
export type ShelterSummary = AnimalShelter & { regNo: string; animalCount: number }

/**
 * 화면에서 쓰는 동물 정보. 검색 API 응답을 `lib/api.ts`의 toAnimal()로 바꿔 만듭니다.
 * score·reasons·distance는 임베딩·태깅·위치 검색이 붙으면 채워지며, 없으면 화면에서 숨깁니다.
 */
export type Animal = {
  /** 구조번호 desertionNo */
  id: string
  noticeNo: string | null
  species: Species
  cat: boolean
  /** 품종 (예: 믹스견, 한국 고양이) */
  kind: string
  /** 수컷 / 암컷 / 성별 미상 */
  gender: string
  /** 추정 나이(살). 모르면 null */
  age: number | null
  /** 화면 표시용 나이 (예: 3살, 60일 미만) */
  ageLabel: string
  kg: number | null
  weightLabel: string
  color: string
  /** 중성화 여부 문구 */
  care: string
  /** 원문 상태 (보호중, 종료(입양) …) */
  state: string
  protectedNow: boolean
  happenDate: string | null
  happenPlace: string | null
  noticeStart: string | null
  noticeEnd: string | null
  center: string
  shelter: AnimalShelter
  /** 공고 특징 문장 (specialMark) */
  quote: string
  photos: string[]
  // 상세에서만 오는 값
  healthCheck?: string | null
  vaccinationCheck?: string | null
  sociality?: string | null
  healthNote?: string | null
  endReason?: string | null
  // 아직 API에 없는 값
  score?: number
  reasons?: MatchReason[]
  distance?: number
  // 사진이 없을 때 쓰는 일러스트 색
  coat: string
  ear: string
  bg: string
}
