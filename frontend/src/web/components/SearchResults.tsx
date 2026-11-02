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
  const { visible, total, hasMore, searched, searching, loadingMore, error, retry, sort, setSort, availableFirst, toggleAvailableFirst, map, toggleMap, loadMore, resetAndSearch, photo } = useSearch()
  const { openAnimal } = useExperience()

  return <section className="results" aria-live="polite">
    <div className="results-heading">
      <div className="results-title"><h2>{searched ? <>검색 결과 <em>{total}</em>마리</> : <>새로운 인연을 기다리는 친구들 <em>{total}</em></>}</h2><span>{searched ? '문장의 단어와 생활 조건이 맞는 공고예요' : '작은 관심이, 한 아이의 봄이 될 수 있어요'}</span></div>
      <div className="result-tools">
        <button className={`available-toggle ${availableFirst ? 'on' : ''}`} onClick={toggleAvailableFirst} aria-pressed={availableFirst}><span className="checkbox">{availableFirst && <Icon name="check" size={12} />}</span>입양 가능 먼저</button>
        <select value={sort} onChange={e => setSort(e.target.value as SortOption)} aria-label="결과 정렬">{sortOptions.map(option => <option key={option}>{option}</option>)}</select>
        <button className={`view-button ${map ? 'on' : ''}`} onClick={toggleMap}><Icon name={map ? 'grid' : 'pin'} size={15} />{map ? '목록으로 보기' : '지도로 보기'}</button>
      </div>
    </div>
    {searched && <p className="demo-search-note">지금은 문장을 단어 단위로만 찾아요. 문장의 뜻{photo ? '과 첨부한 사진' : ''}으로 찾는 인공지능 분석은 아직 연결되어 있지 않아요.</p>}
    {searching
      ? <MascotState kind="loading" />
      : error
        ? <MascotState kind="error" detail={error} serverHint={error.includes('연결')}><button className="primary-button" onClick={retry}>다시 불러오기</button></MascotState>
        : !visible.length
          ? searched ? <MascotState kind="no-results"><button className="primary-button" onClick={resetAndSearch}>조건 넓혀서 다시 찾기</button></MascotState> : <MascotState kind="empty" />
          : map
            ? <ResultMap animals={visible} onOpen={openAnimal} />
            : <div className="animal-grid">{visible.map(a => <PetCard key={a.id} animal={a} onOpen={openAnimal} />)}</div>}
    {!searching && !error && !map && hasMore && <button className="load-more" onClick={loadMore} disabled={loadingMore}>{loadingMore ? '불러오는 중' : '기다리는 친구 더 만나기'} <span>{visible.length} / {total}</span><Icon name="plus" size={15} /></button>}
    <p className="fiction-note">공고 정보는 공공데이터포털 구조동물 API 샘플(2026-10-30 기준)이며, 실제 입양 가능 여부는 보호소에 확인해 주세요.</p>
  </section>
}
