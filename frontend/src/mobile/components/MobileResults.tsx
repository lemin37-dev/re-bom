'use client'

import { Icon } from '@/components/icons/Icon'
import { IllustratedMap } from '@/components/map/IllustratedMap'
import { MascotState } from '@/components/mascot/MascotState'
import { filterLabels, sortOptions } from '@/data/filters'
import { useExperience } from '@/providers/ExperienceProvider'
import { useSearch } from '@/providers/SearchProvider'
import type { FilterKey, SortOption } from '@/types/filters'
import { MobilePetCard } from './MobilePetCard'

/** 앱 검색 결과: 제목·지도 전환 → 정렬 → 적용 조건 → (지도) → 세로 카드 목록 */
export function MobileResults() {
  const { visible, total, hasMore, searched, searching, loadingMore, error, retry, sort, setSort, availableFirst, toggleAvailableFirst, map, toggleMap, filters, openFilters, loadMore, resetAndSearch } = useSearch()
  const { openAnimal } = useExperience()
  // 지도 보기에서는 첫 한 마리만 카드로 보여 줍니다.
  const cards = map ? visible.slice(0, 1) : visible

  return <section className="mobile-results" aria-live="polite">
    <div className="mobile-results-heading"><div><span className="eyebrow">{searched ? '조건에 맞는 공고를 찾았어요' : '작은 관심이 새로운 시작으로'}</span><h2>{searched ? '나와 맞는 친구' : '기다리고 있는 친구'} <span>{total}</span></h2></div><button onClick={toggleMap} className={`mobile-map-toggle ${map ? 'active' : ''}`}><Icon name={map ? 'grid' : 'pin'} size={17} />{map ? '목록' : '지도'}</button></div>
    <div className="mobile-result-tools"><button className={availableFirst ? 'on' : ''} onClick={toggleAvailableFirst} aria-pressed={availableFirst}><span className="checkbox">{availableFirst && <Icon name="check" size={12} />}</span>입양 가능 먼저</button><label><span className="sr-only">결과 정렬</span><select value={sort} onChange={e => setSort(e.target.value as SortOption)}>{sortOptions.map(option => <option key={option}>{option}</option>)}</select></label></div>
    {searched && <div className="mobile-applied-filters">{(Object.keys(filters) as FilterKey[]).map(key => <button key={key} onClick={openFilters}><span>{filterLabels[key]}</span>{filters[key]}</button>)}</div>}
    {!searching && !error && map && visible.length > 0 && <div className="mobile-search-map">
      <IllustratedMap>
        {visible.slice(0, 5).map((a, i) => <button key={a.id} className={`map-pin pin-${i}`} onClick={() => openAnimal(a)}><Icon name="pin" size={15} />{a.shelter.sigungu || a.center.split(' ')[0]}</button>)}
        <span className="map-note">위치를 설명하기 위한 예시 지도</span>
      </IllustratedMap>
      <p>지도의 보호센터를 누르면 친구를 자세히 볼 수 있어요.</p>
    </div>}
    {searching
      ? <MascotState kind="loading" />
      : error
        ? <MascotState kind="error" detail={error} serverHint={error.includes('연결')}><button className="primary-button" onClick={retry}>다시 불러오기</button></MascotState>
        : !visible.length
          ? searched ? <MascotState kind="no-results"><button className="primary-button" onClick={resetAndSearch}>조건 넓혀서 다시 찾기</button></MascotState> : <MascotState kind="empty" />
          : <div className="mobile-pet-list">{cards.map((a, i) => <MobilePetCard key={a.id} animal={a} onOpen={openAnimal} featured={i === 0 && !map} />)}</div>}
    {!searching && !error && !map && hasMore && <button className="mobile-load-more" onClick={loadMore} disabled={loadingMore}>{loadingMore ? '불러오는 중' : '다른 친구도 만나기'}<span>{visible.length} / {total}</span><Icon name="plus" size={17} /></button>}
    <p className="mobile-data-note">공공데이터 구조동물 샘플 · 2026-10-30 기준<br />문장의 뜻·사진으로 찾는 인공지능 분석은 아직 연결되어 있지 않아요.</p>
  </section>
}
