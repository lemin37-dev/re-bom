import type { FilterKey, Filters, SortOption } from '@/types/filters'

export const initialFilters: Filters = { region: '서울·경기', type: '개', age: '2~7살', size: '중형 8~15kg', gender: '무관' }
export const allFilters: Filters = { region: '전국', type: '전체', age: '무관', size: '무관', gender: '무관' }

export const filterLabels: Record<FilterKey, string> = { region: '지역', type: '종류', age: '나이', size: '크기', gender: '성별' }
export const filterOptions: Record<FilterKey, string[]> = {
  region: ['전국', '서울·경기', '인천', '강원', '충청', '전라', '경상', '제주'],
  type: ['전체', '개', '고양이'],
  age: ['무관', '1살 미만', '2~7살', '8살 이상'],
  size: ['무관', '소형 8kg 미만', '중형 8~15kg', '대형 15kg 이상'],
  gender: ['무관', '수컷', '암컷'],
}

export const sortOptions: SortOption[] = ['매칭순', '가까운 순', '공고 종료 임박 순']

/** 검색 첫 화면의 추천 문장 */
export const searchSuggestions = ['낮에 집이 비어도 괜찮은 성견', '아파트에서 키울 소형견', '처음 키우는 고양이']

/** 추천 문장에 맞춰 함께 바꿀 조건 */
export function filtersForSuggestion(text: string, filters: Filters): Filters {
  if (text.includes('고양이')) return { ...filters, type: '고양이', size: '무관' }
  if (text.includes('소형견')) return { ...filters, type: '개', size: '소형 8kg 미만' }
  return filters
}

export const PAGE_SIZE = 8
