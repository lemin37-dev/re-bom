/** 사진 태그 한 개 (검색 API `/tags`). count 는 이 태그가 붙은 보호 중인 아이 수 */
export type TagOption = { code: string; label: string; count: number }

/** 큰 범주 (털 · 귀 모양 · 눈매) 와 그 안의 상세 태그 */
export type TagCategory = { key: string; label: string; tags: TagOption[] }

export type TagCatalog = { categories: TagCategory[]; recommended: string[] }
