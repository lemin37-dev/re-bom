'use client'

import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { allFilters, initialFilters, PAGE_SIZE } from '@/data/filters'
import { findVisibleAnimals } from '@/lib/search'
import type { Animal } from '@/types/animal'
import type { Filters, SortOption } from '@/types/filters'

type SearchState = {
  query: string
  setQuery: (value: string) => void
  /** 첨부 사진: 'sample'은 예시 일러스트, 그 외는 blob URL */
  photo: string | null
  setPhoto: (value: string | null) => void
  filters: Filters
  setFilters: (value: Filters) => void
  sort: SortOption
  setSort: (value: SortOption) => void
  availableFirst: boolean
  toggleAvailableFirst: () => void
  map: boolean
  toggleMap: () => void
  searched: boolean
  setSearched: (value: boolean) => void
  searching: boolean
  centerFilter: string | null
  displayLimit: number
  loadMore: () => void
  visible: Animal[]
  /** 검색 실행. text를 넘기면 검색어도 함께 바꿉니다. */
  search: (text?: string) => void
  /** 조건을 모두 풀고 다시 검색 */
  resetAndSearch: () => void
  /** 특정 보호센터의 아이들만 보기 */
  focusCenter: (center: string) => void
  /** 보호센터 조건 해제 (조건·검색 상태도 처음으로) */
  clearCenter: () => void
  setCenterFilter: (center: string | null) => void
  /** 조건 선택 창 */
  filterOpen: boolean
  openFilters: () => void
  closeFilters: () => void
  applyFilters: (value: Filters) => void
}

const SearchContext = createContext<SearchState | null>(null)

/** 입양 찾기 검색 상태. 웹/앱 모드를 오가도 같은 상태가 유지되도록 최상위에 둡니다. */
export function SearchProvider({ children }: { children: ReactNode }) {
  const [query, setQuery] = useState('')
  const [searched, setSearched] = useState(false)
  const [searching, setSearching] = useState(false)
  const [photo, setPhoto] = useState<string | null>('sample')
  const [filters, setFilters] = useState<Filters>(initialFilters)
  const [filterOpen, setFilterOpen] = useState(false)
  const [sort, setSort] = useState<SortOption>('매칭순')
  const [availableFirst, setAvailableFirst] = useState(false)
  const [map, setMap] = useState(false)
  const [centerFilter, setCenterFilter] = useState<string | null>(null)
  const [displayLimit, setDisplayLimit] = useState(PAGE_SIZE)

  useEffect(() => () => { if (photo?.startsWith('blob:')) URL.revokeObjectURL(photo) }, [photo])

  const visible = useMemo(() => findVisibleAnimals({ searched, filters, centerFilter, sort, availableFirst }), [searched, filters, centerFilter, sort, availableFirst])

  function search(text?: string) {
    setCenterFilter(null)
    if (text !== undefined) setQuery(text)
    setDisplayLimit(PAGE_SIZE)
    setSearching(true)
    window.setTimeout(() => { setSearched(true); setSearching(false) }, 450)
  }

  const value: SearchState = {
    query, setQuery, photo, setPhoto, filters, setFilters, sort, setSort,
    availableFirst, toggleAvailableFirst: () => setAvailableFirst(v => !v),
    map, toggleMap: () => setMap(v => !v),
    searched, setSearched, searching, centerFilter, displayLimit,
    loadMore: () => setDisplayLimit(limit => limit + PAGE_SIZE),
    visible, search,
    resetAndSearch: () => { setFilters(allFilters); search('') },
    focusCenter: center => { setCenterFilter(center); setFilters(allFilters); setQuery(''); setSearched(true); setDisplayLimit(PAGE_SIZE); setMap(false) },
    clearCenter: () => { setCenterFilter(null); setFilters(initialFilters); setSearched(false) },
    setCenterFilter,
    filterOpen,
    openFilters: () => setFilterOpen(true),
    closeFilters: () => setFilterOpen(false),
    applyFilters: next => { setFilters(next); setFilterOpen(false); if (searched) search() },
  }
  return <SearchContext.Provider value={value}>{children}</SearchContext.Provider>
}

export function useSearch() {
  const context = useContext(SearchContext)
  if (!context) throw new Error('useSearch는 SearchProvider 안에서만 사용할 수 있어요.')
  return context
}
