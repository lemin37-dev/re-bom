export type Filters = {
  region: string
  type: string
  age: string
  size: string
  gender: string
}

export type FilterKey = keyof Filters

export type SortOption = '최신 공고 순' | '공고 종료 임박 순'
