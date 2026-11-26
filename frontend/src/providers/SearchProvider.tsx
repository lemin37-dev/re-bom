'use client'

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { allFilters, initialFilters, PAGE_SIZE } from '@/data/filters'
import { isAvailable } from '@/lib/animal'
import { ApiError, fetchAnimals, type AnimalSearchParams } from '@/lib/api'
import { availableFirstOrder, filtersToParams, sortToParam } from '@/lib/search'
import type { Animal } from '@/types/animal'
import type { Filters, SortOption } from '@/types/filters'
import { TAG_SPECIES, type TagSpecies } from '@/types/tags'

/** 보호센터 조건: 등록번호로 찾고, 이름은 화면 표시용 */
export type CenterFilter = { regNo: string; name: string }

type SearchState = {
  query: string
  setQuery: (value: string) => void
  /** 첨부 사진 미리보기 blob URL. 검색하면 사진 벡터로 줄 세움(문장보다 우선) */
  photo: string | null
  setPhoto: (file: File | null) => void
  filters: Filters
  setFilters: (value: Filters) => void
  /** 고른 사진 태그 코드. 누르면 바로 다시 검색합니다 (문장은 입력 중인 것이 아니라 마지막으로 제출한 것만 씀). */
  tags: string[]
  /** 태그 세트를 보여 줄 종 (생활 조건의 종류와 같은 값). 종류가 전체면 null 이라 종부터 고르게 합니다. */
  tagSpecies: TagSpecies | null
  /** 강아지·고양이 중 고르기. 종류 조건을 맞추고 다른 종의 태그는 지웁니다. */
  chooseTagSpecies: (species: TagSpecies) => void
  /** 태그 고르기·빼기 (종을 고른 뒤에만 보임) */
  toggleTag: (code: string) => void
  clearTags: () => void
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
  /** 태그로 검색했을 때 고른 범주가 모두 맞는 아이 수 (목록 맨 앞). 태그 없이 검색하면 null */
  matched: number | null
  hasMore: boolean
  loadMore: () => void
  /** 검색 실행. text·nextFilters·nextTags를 넘기면 검색어·조건·태그도 함께 바꿉니다. */
  search: (text?: string, nextFilters?: Filters, nextTags?: string[]) => void
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
  const [photoFile, setPhotoFile] = useState<File | null>(null)
  const photo = useMemo(() => (photoFile ? URL.createObjectURL(photoFile) : null), [photoFile])
  const [filters, setFilters] = useState<Filters>(initialFilters)
  const [tags, setTags] = useState<string[]>([])
  const [filterOpen, setFilterOpen] = useState(false)
  const [sort, setSort] = useState<SortOption>('최신 공고 순')
  const [availableFirst, setAvailableFirst] = useState(false)
  const [map, setMap] = useState(false)
  const [centerFilter, setCenterFilter] = useState<CenterFilter | null>(null)
  // 검색 버튼을 누른 시점의 검색어·조건 (입력 중인 값과 구분)
  const [submitted, setSubmitted] = useState<{ query: string; filters: Filters; tags: string[]; photo: File | null; seq: number }>({ query: '', filters: initialFilters, tags: [], photo: null, seq: 0 })

  // 불러온 결과는 어떤 조건(key)으로 받았는지와 함께 둡니다. key가 지금 조건과 다르면 '불러오는 중'입니다.
  type Result = { key: string; items: Animal[]; total: number; matched: number | null; page: number; error: string | null }
  const [result, setResult] = useState<Result>({ key: '', items: [], total: 0, matched: null, page: 0, error: null })
  const [retrySeq, setRetrySeq] = useState(0)
  const [loadingMore, setLoadingMore] = useState(false)
  const moreAbort = useRef<AbortController | null>(null)

  useEffect(() => () => { if (photo) URL.revokeObjectURL(photo) }, [photo])

  // 보호 중인 아이만 보여 줍니다. 검색 전에는 최신 공고, 검색 후에는 검색어·조건을 적용합니다.
  const params = useMemo<AnimalSearchParams>(() => ({
    state: 'protect',
    sort: sortToParam(sort),
    size: PAGE_SIZE,
    ...(centerFilter ? { shelter: centerFilter.regNo } : searched ? { ...filtersToParams(submitted.filters), q: submitted.query, tags: submitted.tags } : {}),
  }), [sort, centerFilter, searched, submitted])
  const searchPhoto = !centerFilter && searched ? submitted.photo : null
  const key = `${JSON.stringify(params)}#${submitted.seq}#${retrySeq}#${searchPhoto ? 'photo' : ''}`
  const searching = result.key !== key

  useEffect(() => {
    const controller = new AbortController()
    moreAbort.current?.abort()
    fetchAnimals({ ...params, page: 1 }, controller.signal, searchPhoto)
      .then(res => setResult({ key, items: res.items, total: res.total, matched: res.matched, page: 1, error: null }))
      .catch(e => { if (!controller.signal.aborted) setResult({ key, items: [], total: 0, matched: null, page: 0, error: e instanceof ApiError ? e.message : '목록을 불러오지 못했어요.' }) })
    return () => controller.abort()
  }, [params, key, searchPhoto])

  const loadMore = useCallback(() => {
    if (loadingMore || searching) return
    const controller = new AbortController()
    moreAbort.current = controller
    setLoadingMore(true)
    fetchAnimals({ ...params, page: result.page + 1 }, controller.signal, searchPhoto)
      .then(res => setResult(prev => prev.key !== key ? prev : { ...prev, items: [...prev.items, ...res.items.filter(a => !prev.items.some(p => p.id === a.id))], total: res.total, page: prev.page + 1 }))
      .catch(e => { if (!controller.signal.aborted) setResult(prev => ({ ...prev, error: e instanceof ApiError ? e.message : '목록을 더 불러오지 못했어요.' })) })
      .finally(() => setLoadingMore(false))
  }, [loadingMore, searching, params, result.page, key, searchPhoto])

  const items = result.items
  const total = result.total
  const error = searching ? null : result.error
  const visible = useMemo(() => availableFirst ? availableFirstOrder(items, isAvailable) : items, [items, availableFirst])

  function search(text?: string, nextFilters?: Filters, nextTags?: string[]) {
    setCenterFilter(null)
    const q = text !== undefined ? text : query
    if (text !== undefined) setQuery(text)
    if (nextFilters) setFilters(nextFilters)
    if (nextTags) setTags(nextTags)
    setSubmitted(prev => ({ query: q, filters: nextFilters ?? filters, tags: nextTags ?? tags, photo: photoFile, seq: prev.seq + 1 }))
    setSearched(true)
  }

  // 태그는 누르는 즉시 검색하므로, 아직 제출하지 않은 입력 문장이 섞이지 않게 마지막 제출 문장을 그대로 씁니다.
  function searchTags(nextTags: string[], nextFilters: Filters = filters) {
    setCenterFilter(null)
    setTags(nextTags)
    setFilters(nextFilters)
    setSubmitted(prev => ({ ...prev, filters: nextFilters, tags: nextTags, seq: prev.seq + 1 }))
    setSearched(true)
  }

  const tagSpecies = TAG_SPECIES.find(s => s.filterType === filters.type)?.key ?? null

  function chooseTagSpecies(species: TagSpecies) {
    if (species === tagSpecies) return
    const next = { ...filters, type: TAG_SPECIES.find(s => s.key === species)!.filterType }
    if (searched || tags.length) searchTags([], next)
    else { setFilters(next); setTags([]) }
  }

  const value: SearchState = {
    query, setQuery, photo, setPhoto: setPhotoFile, filters, setFilters, sort, setSort,
    tags,
    tagSpecies, chooseTagSpecies,
    toggleTag: code => searchTags(tags.includes(code) ? tags.filter(t => t !== code) : [...tags, code]),
    clearTags: () => searchTags([]),
    availableFirst, toggleAvailableFirst: () => setAvailableFirst(v => !v),
    map, toggleMap: () => setMap(v => !v),
    searched, setSearched, searching, loadingMore, error,
    retry: () => setRetrySeq(n => n + 1),
    centerFilter, visible, total, matched: searching ? null : result.matched, hasMore: items.length < total, loadMore,
    search,
    resetAndSearch: () => search('', allFilters, []),
    focusCenter: center => { setCenterFilter(center); setFilters(allFilters); setTags([]); setQuery(''); setSubmitted(prev => ({ ...prev, query: '' })); setSearched(true); setMap(false) },
    clearCenter: () => { setCenterFilter(null); setFilters(initialFilters); setTags([]); setSearched(false) },
    setCenterFilter,
    filterOpen,
    openFilters: () => setFilterOpen(true),
    closeFilters: () => setFilterOpen(false),
    // 종류가 바뀌면 이전 종의 태그는 맞지 않으므로 지웁니다.
    applyFilters: next => { setFilterOpen(false); const nextTags = next.type === filters.type ? tags : []; if (searched) search(undefined, next, nextTags); else { setFilters(next); setTags(nextTags) } },
  }
  return <SearchContext.Provider value={value}>{children}</SearchContext.Provider>
}

export function useSearch() {
  const context = useContext(SearchContext)
  if (!context) throw new Error('useSearch는 SearchProvider 안에서만 사용할 수 있어요.')
  return context
}
