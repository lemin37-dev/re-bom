'use client'

import { Icon } from '@/components/icons/Icon'
import { filtersForSuggestion, searchSuggestions } from '@/data/filters'
import { useSearch } from '@/providers/SearchProvider'

export function SearchSuggestions() {
  const { filters, setFilters, search } = useSearch()
  return <div className="suggestions"><span>이렇게 찾아보세요</span>
    {searchSuggestions.map(text => <button key={text} onClick={() => { setFilters(filtersForSuggestion(text, filters)); search(text) }}>{text}<Icon name="arrow" size={13} /></button>)}
  </div>
}
