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
  const { visible, searched, searching, sort, setSort, availableFirst, toggleAvailableFirst, map, toggleMap, filters, openFilters, displayLimit, loadMore, resetAndSearch } = useSearch()
  const { openAnimal } = useExperience()
  // 지도 보기에서는 가장 잘 맞는 한 마리만 카드로 보여 줍니다.
  const cards = map ? visible.slice(0, 1) : visible.slice(0, displayLimit)

  return <section className="mobile-results" aria-live="polite">
    <div className="mobile-results-heading"><div><span className="eyebrow">{searched ? '함께 살아갈 이유를 찾았어요' : '작은 관심이 새로운 시작으로'}</span><h2>{searched ? '나와 맞는 친구' : '기다리고 있는 친구'} <span>{visible.length}</span></h2></div><button onClick={toggleMap} className={`mobile-map-toggle ${map ? 'active' : ''}`}><Icon name={map ? 'grid' : 'pin'} size={17} />{map ? '목록' : '지도'}</button></div>
    <div className="mobile-result-tools"><button className={availableFirst ? 'on' : ''} onClick={toggleAvailableFirst} aria-pressed={availableFirst}><span className="checkbox">{availableFirst && <Icon name="check" size={12} />}</span>입양 가능 먼저</button><label><span className="sr-only">결과 정렬</span><select value={sort} onChange={e => setSort(e.target.value as SortOption)}>{sortOptions.map(option => <option key={option}>{option}</option>)}</select></label></div>
    {searched && <div className="mobile-applied-filters">{(Object.keys(filters) as FilterKey[]).map(key => <button key={key} onClick={openFilters}><span>{filterLabels[key]}</span>{filters[key]}</button>)}</div>}
    {!searching && map && visible.length > 0 && <div className="mobile-search-map">
      <IllustratedMap>
        {visible.slice(0, 5).map((a, i) => <button key={a.id} className={`map-pin pin-${i}`} onClick={() => openAnimal(a)}><Icon name="pin" size={15} />{a.center.split(' ')[0]}</button>)}
        <span className="map-note">위치를 설명하기 위한 예시 지도</span>
      </IllustratedMap>
      <p>지도의 보호센터를 누르면 친구를 자세히 볼 수 있어요.</p>
    </div>}
    {searching
      ? <MascotState loading />
      : !visible.length
        ? <MascotState><button className="primary-button" onClick={resetAndSearch}>조건 넓혀서 다시 찾기</button></MascotState>
        : <div className="mobile-pet-list">{cards.map((a, i) => <MobilePetCard key={a.id} animal={a} onOpen={openAnimal} featured={i === 0 && !map} />)}</div>}
    {!searching && !map && visible.length > displayLimit && <button className="mobile-load-more" onClick={loadMore}>다른 친구도 만나기<span>{displayLimit} / {visible.length}</span><Icon name="plus" size={17} /></button>}
    <p className="mobile-data-note">예시 화면 · 가상 데이터<br />문장·사진의 실제 인공지능 분석은 연결되어 있지 않아요.</p>
  </section>
}
