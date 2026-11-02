'use client'

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { allFilters, initialFilters, PAGE_SIZE } from '@/data/filters'
import { isAvailable } from '@/lib/animal'
import { ApiError, fetchAnimals, type AnimalSearchParams } from '@/lib/api'
import { availableFirstOrder, filtersToParams, sortToParam } from '@/lib/search'
import type { Animal } from '@/types/animal'
import type { Filters, SortOption } from '@/types/filters'

/** 보호센터 조건: 등록번호로 찾고, 이름은 화면 표시용 */
export type CenterFilter = { regNo: string; name: string }

type SearchState = {
  query: string
  setQuery: (value: string) => void
  /** 첨부 사진 blob URL (사진 검색은 아직 서버에 연결되지 않아 결과에는 반영되지 않음) */
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
  /** 첫 페이지를 불러오는 중 */
  searching: boolean
  /** 다음 페이지를 불러오는 중 */
  loadingMore: boolean
  /** 서버 연결 실패 등 오류 문구 */
  error: string | null
  retry: () => void
  centerFilter: CenterFilter | null
  /** 지금까지 불러온 동물 */
  visible: Animal[]
  /** 조건에 맞는 전체 수 */
  total: number
  hasMore: boolean
  loadMore: () => void
  /** 검색 실행. text·nextFilters를 넘기면 검색어·조건도 함께 바꿉니다. */
  search: (text?: string, nextFilters?: Filters) => void
  /** 조건을 모두 풀고 다시 검색 */
  resetAndSearch: () => void
  /** 특정 보호센터의 아이들만 보기 */
  focusCenter: (center: CenterFilter) => void
  /** 보호센터 조건 해제 (조건·검색 상태도 처음으로) */
  clearCenter: () => void
  setCenterFilter: (center: CenterFilter | null) => void
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
  const [photo, setPhoto] = useState<string | null>(null)
  const [filters, setFilters] = useState<Filters>(initialFilters)
  const [filterOpen, setFilterOpen] = useState(false)
  const [sort, setSort] = useState<SortOption>('최신 공고 순')
  const [availableFirst, setAvailableFirst] = useState(false)
  const [map, setMap] = useState(false)
  const [centerFilter, setCenterFilter] = useState<CenterFilter | null>(null)
  // 검색 버튼을 누른 시점의 검색어·조건 (입력 중인 값과 구분)
  const [submitted, setSubmitted] = useState<{ query: string; filters: Filters; seq: number }>({ query: '', filters: initialFilters, seq: 0 })

  // 불러온 결과는 어떤 조건(key)으로 받았는지와 함께 둡니다. key가 지금 조건과 다르면 '불러오는 중'입니다.
  type Result = { key: string; items: Animal[]; total: number; page: number; error: string | null }
  const [result, setResult] = useState<Result>({ key: '', items: [], total: 0, page: 0, error: null })
  const [retrySeq, setRetrySeq] = useState(0)
  const [loadingMore, setLoadingMore] = useState(false)
  const moreAbort = useRef<AbortController | null>(null)

  useEffect(() => () => { if (photo?.startsWith('blob:')) URL.revokeObjectURL(photo) }, [photo])

  // 보호 중인 아이만 보여 줍니다. 검색 전에는 최신 공고, 검색 후에는 검색어·조건을 적용합니다.
  const params = useMemo<AnimalSearchParams>(() => ({
    state: 'protect',
    sort: sortToParam(sort),
    size: PAGE_SIZE,
    ...(centerFilter ? { shelter: centerFilter.regNo } : searched ? { ...filtersToParams(submitted.filters), q: submitted.query } : {}),
  }), [sort, centerFilter, searched, submitted])
  const key = `${JSON.stringify(params)}#${submitted.seq}#${retrySeq}`
  const searching = result.key !== key

  useEffect(() => {
    const controller = new AbortController()
    moreAbort.current?.abort()
    fetchAnimals({ ...params, page: 1 }, controller.signal)
      .then(res => setResult({ key, items: res.items, total: res.total, page: 1, error: null }))
      .catch(e => { if (!controller.signal.aborted) setResult({ key, items: [], total: 0, page: 0, error: e instanceof ApiError ? e.message : '목록을 불러오지 못했어요.' }) })
    return () => controller.abort()
  }, [params, key])

  const loadMore = useCallback(() => {
    if (loadingMore || searching) return
    const controller = new AbortController()
    moreAbort.current = controller
    setLoadingMore(true)
    fetchAnimals({ ...params, page: result.page + 1 }, controller.signal)
      .then(res => setResult(prev => prev.key !== key ? prev : { ...prev, items: [...prev.items, ...res.items.filter(a => !prev.items.some(p => p.id === a.id))], total: res.total, page: prev.page + 1 }))
      .catch(e => { if (!controller.signal.aborted) setResult(prev => ({ ...prev, error: e instanceof ApiError ? e.message : '목록을 더 불러오지 못했어요.' })) })
      .finally(() => setLoadingMore(false))
  }, [loadingMore, searching, params, result.page, key])

  const items = result.items
  const total = result.total
  const error = searching ? null : result.error
  const visible = useMemo(() => availableFirst ? availableFirstOrder(items, isAvailable) : items, [items, availableFirst])

  function search(text?: string, nextFilters?: Filters) {
    setCenterFilter(null)
    const q = text !== undefined ? text : query
    if (text !== undefined) setQuery(text)
    if (nextFilters) setFilters(nextFilters)
    setSubmitted(prev => ({ query: q, filters: nextFilters ?? filters, seq: prev.seq + 1 }))
    setSearched(true)
  }

  const value: SearchState = {
    query, setQuery, photo, setPhoto, filters, setFilters, sort, setSort,
    availableFirst, toggleAvailableFirst: () => setAvailableFirst(v => !v),
    map, toggleMap: () => setMap(v => !v),
    searched, setSearched, searching, loadingMore, error,
    retry: () => setRetrySeq(n => n + 1),
    centerFilter, visible, total, hasMore: items.length < total, loadMore,
    search,
    resetAndSearch: () => search('', allFilters),
    focusCenter: center => { setCenterFilter(center); setFilters(allFilters); setQuery(''); setSearched(true); setMap(false) },
    clearCenter: () => { setCenterFilter(null); setFilters(initialFilters); setSearched(false) },
    setCenterFilter,
    filterOpen,
    openFilters: () => setFilterOpen(true),
    closeFilters: () => setFilterOpen(false),
    applyFilters: next => { setFilterOpen(false); if (searched) search(undefined, next); else setFilters(next) },
  }
  return <SearchContext.Provider value={value}>{children}</SearchContext.Provider>
}

export function useSearch() {
  const context = useContext(SearchContext)
  if (!context) throw new Error('useSearch는 SearchProvider 안에서만 사용할 수 있어요.')
  return context
}
