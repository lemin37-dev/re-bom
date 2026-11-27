/** 우리 지역 입양 지원 스냅샷 (`data/adoption-support.json`, scripts/guide/build_adoption_support.py 가 만듦) */

export type ProgramKind = 'cost' | 'insurance'
export type OrdinanceKind = 'cost' | 'insurance' | 'education' | 'center' | 'event' | 'aftercare' | 'goods' | 'fee' | 'basic'

/** 원문에서 뽑은 쉬운 요약 (scripts/guide/build_adoption_support.py benefit_facts) */
export type BenefitFacts = {
  /** 예: '입양 비용 지원 최대 25만원', '펫보험 가입비 지원 15만원', '입양장려금' */
  headline: string
  amountMan: number | null
  /** '쓴 비용의 N%까지' */
  percent: number | null
  /** 지원되는 비용 항목 (예: 질병 진단, 치료, 예방접종 …) */
  items: string[]
  /** 받을 수 있는 조건 (예: 이 지역 보호센터에서 입양, 내장형 동물등록 완료 …) */
  conditions: string[]
  /** 예: '입양 후 1년 안에 신청', '언제든 신청' */
  applyWithin: string
  /** 신청 방법 (방문, 이메일, 우편 …) */
  channels: string[]
  /** 예산·사업량이 끝나면 마감 */
  budgetLimited: boolean
}

/** 신청할 수 있는 지원 (행정안전부 대한민국 공공서비스(혜택) 정보) */
export type SupportProgram = {
  id: string
  sido: string | null
  /** 없으면 시·도 사업 */
  sigungu: string | null
  kinds: ProgramKind[]
  name: string
  summary: string
  target: string
  content: string
  deadline: string
  method: string
  documents: string
  /** 공공기관 문의처 (부서/전화) */
  contact: string
  law: string
  ordinance: string
  /** YYYYMMDD */
  updatedAt: string
  benefit: BenefitFacts
}

/** 조례에 근거가 있는 지원 (국가법령정보센터 자치법규, 자동 분류 · 검토 전) */
export type SupportOrdinance = {
  sido: string
  sigungu: string | null
  name: string
  /** 지원 종류 → 조문 번호 (예: { cost: ['제18조'] }) */
  kinds: Partial<Record<OrdinanceKind, string[]>>
  enforcedAt: string
  reviewed: boolean
}

/** 반려동물 축제 (전국문화축제 표준데이터) */
export type PetFestival = {
  name: string
  /** YYYY-MM-DD */
  start: string
  end: string
  place: string | null
  address: string | null
  sido: string | null
  sigungu: string | null
  host: string | null
  lat: number | null
  lng: number | null
}

/** 지역 대표 위치 (OpenStreetMap Nominatim). sigungu 가 null 이면 시·도 */
export type RegionCoord = { sido: string; sigungu: string | null; lat: number; lng: number; approx: boolean }

export type AdoptionSupportSnapshot = {
  generatedAt: string
  sources: { programs: string; ordinances: string; festivals: string; coords: string }
  regions: RegionCoord[]
  programs: SupportProgram[]
  ordinances: SupportOrdinance[]
  festivals: PetFestival[]
}
