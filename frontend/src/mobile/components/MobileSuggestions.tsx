'use client'

import { Icon } from '@/components/icons/Icon'
import { filtersForSuggestion, searchSuggestions } from '@/data/filters'
import { useSearch } from '@/providers/SearchProvider'

/** 추천 문장마다 종류·크기 조건을 함께 맞춥니다 (웹과 같은 규칙). */
export function MobileSuggestions() {
  const { filters, search } = useSearch()
  return <div className="mobile-suggestions"><span>이렇게 시작해도 좋아요</span>
    <div>{searchSuggestions.map(text => <button key={text} onClick={() => search(text, filtersForSuggestion(text, filters))}>{text}<Icon name="arrow" size={14} /></button>)}</div>
  </div>
}
