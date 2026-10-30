import { animals, searchAnimals } from '@/data/animals'
import type { Animal } from '@/types/animal'
import type { Filters, SortOption } from '@/types/filters'

function matchesFilters(a: Animal, filters: Filters) {
  return (
    (filters.type === '전체' || (filters.type === '고양이' ? a.cat : !a.cat)) &&
    (filters.gender === '무관' || a.gender === filters.gender) &&
    (filters.age === '무관' || (filters.age === '2~7살' ? a.age >= 2 && a.age <= 7 : filters.age === '8살 이상' ? a.age >= 8 : a.age < 1)) &&
    (filters.size === '무관' || (filters.size.startsWith('소형') ? a.kg < 8 : filters.size.startsWith('중형') ? a.kg >= 8 && a.kg <= 15 : a.kg > 15)) &&
    (filters.region === '전국' || filters.region === '서울·경기')
  )
}

type SearchParams = {
  searched: boolean
  filters: Filters
  centerFilter: string | null
  sort: SortOption
  availableFirst: boolean
}

/** 검색 상태에 맞는 동물 목록 (필터 → 센터 → 정렬 순으로 적용) */
export function findVisibleAnimals({ searched, filters, centerFilter, sort, availableFirst }: SearchParams): Animal[] {
  let visible = searched ? searchAnimals.filter(a => matchesFilters(a, filters)) : animals
  if (centerFilter) visible = searchAnimals.filter(a => a.center === centerFilter)
  visible = [...visible].sort((a, b) =>
    sort === '가까운 순' ? a.distance - b.distance : sort === '공고 종료 임박 순' ? Number(a.available) - Number(b.available) : b.score - a.score,
  )
  if (availableFirst) visible.sort((a, b) => Number(b.available) - Number(a.available))
  return visible
}
