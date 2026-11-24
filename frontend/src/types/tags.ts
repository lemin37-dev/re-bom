/**
 * 고른 태그에 대한 공고의 단계 (검색 API `tagMatch`). 이 순서대로 앞에 옵니다.
 * all 모두 맞음 · some 일부 맞음 · unknown 고른 범주의 태그 정보 없음 · other 다른 태그
 */
export type TagMatch = 'all' | 'some' | 'unknown' | 'other'
export const TAG_MATCH_ORDER: TagMatch[] = ['all', 'some', 'unknown', 'other']

/** 공고에 붙은 사진 태그 (검색 API `photoTags`) */
export type PhotoTag = { code: string; label: string }

/** 사진 태그 한 개 (검색 API `/tags`). count 는 이 태그가 붙은 보호 중인 아이 수 */
export type TagOption = { code: string; label: string; count: number }

/** 큰 범주 (털 · 귀 모양 · 눈매) 와 그 안의 상세 태그 */
export type TagCategory = { key: string; label: string; tags: TagOption[] }

/** 태그 세트를 나누는 종. 생활 조건의 '종류'(개 · 고양이)와 같은 값 */
export type TagSpecies = 'dog' | 'cat'
export const TAG_SPECIES: { key: TagSpecies; label: string; filterType: string }[] = [
  { key: 'dog', label: '강아지', filterType: '개' },
  { key: 'cat', label: '고양이', filterType: '고양이' },
]

/** 한 종의 태그 세트. 아직 태그 세트가 없는 종은 categories 가 비어 있음 */
export type TagCatalog = { species: TagSpecies; categories: TagCategory[]; recommended: string[] }
