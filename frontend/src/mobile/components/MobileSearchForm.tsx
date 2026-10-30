'use client'

import { useRef } from 'react'
import { AnimalArt } from '@/components/animal/AnimalArt'
import { Icon } from '@/components/icons/Icon'
import { animals } from '@/data/animals'
import { useSearch } from '@/providers/SearchProvider'

/** 앱 검색 박스: 여러 줄 문장 입력 · 사진 첨부 · 생활 조건 요약 */
export function MobileSearchForm() {
  const { query, setQuery, photo, setPhoto, filters, openFilters, search, searching } = useSearch()
  const file = useRef<HTMLInputElement>(null)
  const pickPhoto = () => file.current?.click()

  return <form className="mobile-search-box" onSubmit={e => { e.preventDefault(); search() }}>
    <label className="mobile-query-label" htmlFor="mobile-query">어떤 친구와 함께하고 싶나요?</label>
    <div className="mobile-query"><Icon name="search" size={21} /><textarea id="mobile-query" value={query} onChange={e => setQuery(e.target.value)} rows={2} placeholder="아파트에서 함께 지낼, 조용하고 산책을 좋아하는 친구" /></div>
    <div className="mobile-attachments">
      {photo
        ? <div className="mobile-photo-chip">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <button type="button" onClick={pickPhoto} aria-label="첨부 사진 바꾸기"><span className="mobile-photo-thumb">{photo === 'sample' ? <AnimalArt animal={animals[0]} small /> : <img src={photo} alt="첨부한 동물 사진" />}</span><span><b>사진 1장</b>닮은 친구도 함께 찾아요</span></button>
            <button type="button" onClick={() => setPhoto(null)} aria-label="첨부 사진 삭제"><Icon name="close" size={16} /></button>
          </div>
        : <button className="mobile-add-photo" type="button" onClick={pickPhoto}><Icon name="image" />사진으로도 찾기<Icon name="plus" size={15} /></button>}
      <input ref={file} type="file" accept="image/*" className="hidden-input" onChange={e => { const selected = e.target.files?.[0]; if (selected) setPhoto(URL.createObjectURL(selected)); e.target.value = '' }} />
    </div>
    <div className="mobile-condition-row"><button type="button" className="mobile-filter-trigger" onClick={openFilters}><Icon name="grid" size={16} />생활 조건<span>5</span><Icon name="plus" size={14} /></button><span>{filters.region} · {filters.type} · {filters.size.split(' ')[0]}</span></div>
    <button type="submit" className="peach-button mobile-search-submit" disabled={searching}><Icon name="search" size={19} />{searching ? '맞는 친구를 찾고 있어요' : '나와 맞는 친구 찾아보기'}<Icon name="arrow" size={18} /></button>
    <div className="mobile-source-caption"><span className="source green">문장</span>{photo && <span className="source peach">사진</span>}<span className="source sage">조건</span><span>한 번에 함께 살펴요</span></div>
  </form>
}
