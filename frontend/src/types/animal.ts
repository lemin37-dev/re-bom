/** 매칭 이유 한 줄: [표시 문구, 근거 출처('문장' | '사진' | '조건')] */
export type MatchReason = [label: string, source: string]

export type Animal = {
  id: number
  kind: string
  gender: string
  age: number
  kg: number
  color: string
  care: string
  score: number
  available: boolean
  center: string
  distance: number
  quote: string
  reasons: MatchReason[]
  coat: string
  ear: string
  bg: string
  cat?: boolean
}

export type Shelter = {
  name: string
  region: string
  district: string
  address: string
  phone: string
  hours: string
  type: '직영' | '위탁'
  distance: number
  count: number
}
