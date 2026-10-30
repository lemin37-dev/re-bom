'use client'

import { Icon } from '@/components/icons/Icon'
import { searchSuggestions } from '@/data/filters'
import { useSearch } from '@/providers/SearchProvider'
import type { Filters } from '@/types/filters'

/** 앱에서는 추천 문장마다 종류·크기 조건을 함께 맞춥니다. */
function suggestionFilters(index: number, filters: Filters): Filters {
  return { ...filters, type: index === 2 ? '고양이' : '개', size: index === 2 ? '무관' : index === 1 ? '소형 8kg 미만' : '중형 8~15kg' }
}

export function MobileSuggestions() {
  const { filters, setFilters, search } = useSearch()
  return <div className="mobile-suggestions"><span>이렇게 시작해도 좋아요</span>
    <div>{searchSuggestions.map((text, i) => <button key={text} onClick={() => { setFilters(suggestionFilters(i, filters)); search(text) }}>{text}<Icon name="arrow" size={14} /></button>)}</div>
  </div>
}
