import type { TagMatch } from './tags'

/**
 * 순위 근거 (검색 API `rank`·`debug`). API 를 REBOM_ENV=dev 로 띄웠을 때만 오고, prod 응답에는 키가 없습니다.
 * 화면은 값이 올 때만 카드·목록 위에 보여 줍니다 (따로 켜는 스위치 없음).
 */
export type RankFilterHit = { name: string; condition: string; value: string | null }
export type RankTagCheck = {
  category: string
  label: string
  /** true 면 반대일 때 결과에서 빠짐 (지금은 털만) */
  exclusive: boolean
  picked: string[]
  have: string[]
  /** match 맞음 · other 다름 · unknown 정보 없음 */
  result: 'match' | 'other' | 'unknown'
}
export type RankVector = { kind: string; version: string; model: string; distance: number | null; similarity: number | null }
export type RankDebug = {
  /** 전체 결과에서 몇 번째 (API 순서, 1부터). '입양 가능 먼저'는 화면에서 다시 줄 세우므로 이 값과 다를 수 있음 */
  position: number
  filters: RankFilterHit[]
  tagMatch: TagMatch | null
  tags: RankTagCheck[]
  vector: RankVector | null
  /** 마지막 정렬 칸: happenDate / noticeEnd */
  sortKey: string
  sortValue: string | null
}
export type PageDebug = {
  env: string
  dataMode: string
  /** 정렬 순서 설명 (위에서부터 적용) */
  order: string[]
  excluded: number
  vector: { kind: string; version: string; model: string; dim: number } | null
  /** 질의 벡터가 있을 때만: 필터를 통과한 공고 전체의 similarity 분포 (털 반대 제외 전) */
  similarity?: SimilarityStats | null
}
export type SimilarityStats = {
  scored: number
  /** 거리가 없는 공고 수 (벡터 없음 또는 종료 공고). 맨 뒤에 놓임 */
  missing: number
  top: number | null
  /** nth 가 몇 번째 값인지 (100) */
  nthRank: number
  nth: number | null
  median: number | null
}
