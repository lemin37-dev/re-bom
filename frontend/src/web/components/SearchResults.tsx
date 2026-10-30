'use client'

import { PetCard } from '@/components/animal/PetCard'
import { Icon } from '@/components/icons/Icon'
import { MascotState } from '@/components/mascot/MascotState'
import { sortOptions } from '@/data/filters'
import { useExperience } from '@/providers/ExperienceProvider'
import { useSearch } from '@/providers/SearchProvider'
import type { SortOption } from '@/types/filters'
import { ResultMap } from './ResultMap'

/** 웹 검색 결과: 제목·정렬 도구 + 카드 그리드 또는 지도 */
export function SearchResults() {
  const { visible, searched, searching, sort, setSort, availableFirst, toggleAvailableFirst, map, toggleMap, displayLimit, loadMore, resetAndSearch } = useSearch()
  const { openAnimal } = useExperience()

  return <section className="results" aria-live="polite">
    <div className="results-heading">
      <div className="results-title"><h2>{searched ? <>매칭 결과 <em>{visible.length}</em>마리</> : <>새로운 인연을 기다리는 친구들 <em>{visible.length}</em></>}</h2><span>{searched ? '문장 · 사진 · 조건이 잘 맞는 순서예요' : '작은 관심이, 한 아이의 봄이 될 수 있어요'}</span></div>
      <div className="result-tools">
        <button className={`available-toggle ${availableFirst ? 'on' : ''}`} onClick={toggleAvailableFirst} aria-pressed={availableFirst}><span className="checkbox">{availableFirst && <Icon name="check" size={12} />}</span>입양 가능 먼저</button>
        <select value={sort} onChange={e => setSort(e.target.value as SortOption)} aria-label="결과 정렬">{sortOptions.map(option => <option key={option}>{option}</option>)}</select>
        <button className={`view-button ${map ? 'on' : ''}`} onClick={toggleMap}><Icon name={map ? 'grid' : 'pin'} size={15} />{map ? '목록으로 보기' : '지도로 보기'}</button>
      </div>
    </div>
    {searched && <p className="demo-search-note">예시 데이터로 검색한 결과예요. 문장·사진의 실제 인공지능 분석은 연결되어 있지 않아요.</p>}
    {searching
      ? <MascotState loading />
      : !visible.length
        ? <MascotState><button className="primary-button" onClick={resetAndSearch}>조건 넓혀서 다시 찾기</button></MascotState>
        : map
          ? <ResultMap animals={visible} onOpen={openAnimal} />
          : <div className="animal-grid">{visible.slice(0, displayLimit).map(a => <PetCard key={a.id} animal={a} onOpen={openAnimal} />)}</div>}
    {!searching && !map && visible.length > displayLimit && <button className="load-more" onClick={loadMore}>기다리는 친구 더 만나기 <span>{Math.min(displayLimit, visible.length)} / {visible.length}</span><Icon name="plus" size={15} /></button>}
    <p className="fiction-note">동물과 보호센터 정보는 서비스 이해를 위한 가상 데이터입니다.</p>
  </section>
}
