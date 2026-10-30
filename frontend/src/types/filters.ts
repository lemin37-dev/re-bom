export type Filters = {
  region: string
  type: string
  age: string
  size: string
  gender: string
}

export type FilterKey = keyof Filters

export type SortOption = '매칭순' | '가까운 순' | '공고 종료 임박 순'
