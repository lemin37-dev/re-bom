'use client'

import { useRef } from 'react'
import { AnimalArt } from '@/components/animal/AnimalArt'
import { Icon } from '@/components/icons/Icon'
import { animals } from '@/data/animals'
import { filterLabels } from '@/data/filters'
import { useSearch } from '@/providers/SearchProvider'
import type { FilterKey } from '@/types/filters'

/** 웹 검색 카드: 문장 입력 · 사진 첨부 · 조건 칩 */
export function SearchCard() {
  const { query, setQuery, photo, setPhoto, filters, openFilters, search, searching } = useSearch()
  const fileRef = useRef<HTMLInputElement>(null)
  const pickPhoto = () => fileRef.current?.click()

  return <section className="search-card" aria-label="입양 친구 검색">
    <form className="search-top" onSubmit={e => { e.preventDefault(); search() }}>
      <div className="sentence-input"><Icon name="search" size={23} /><input aria-label="찾고 싶은 친구를 문장으로 입력" value={query} onChange={e => setQuery(e.target.value)} placeholder="아파트에서 함께 지낼, 조용하고 산책을 좋아하는 중형견" /></div>
      {photo
        ? <div className="photo-chip">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <button className="photo-preview" type="button" onClick={pickPhoto} aria-label="첨부 사진 변경">{photo === 'sample' ? <AnimalArt animal={animals[0]} small /> : <img src={photo} alt="첨부한 동물 사진" />}</button>
            <button className="photo-copy" type="button" onClick={pickPhoto}><strong>사진 1장</strong><span>이 아이와 닮은 친구</span></button>
            <button type="button" className="icon-button" onClick={() => setPhoto(null)} aria-label="첨부 사진 삭제"><Icon name="close" size={16} /></button>
          </div>
        : <button type="button" className="photo-add" onClick={pickPhoto}><Icon name="image" />사진으로 찾기</button>}
      <input ref={fileRef} className="hidden-input" type="file" accept="image/*" onChange={e => { const file = e.target.files?.[0]; if (file) setPhoto(URL.createObjectURL(file)); e.target.value = '' }} />
      <button className="search-button" type="submit" disabled={searching}><Icon name="search" size={20} />{searching ? '찾고 있어요' : '찾아보기'}</button>
    </form>
    <div className="search-bottom">
      <div className="filter-chips">
        {(Object.keys(filters) as FilterKey[]).map(key => <button key={key} className="filter-chip" onClick={openFilters}><span>{filterLabels[key]}</span> {filters[key]}</button>)}
        <button className="add-filter" onClick={openFilters}><Icon name="plus" size={13} />조건 추가</button>
      </div>
      <div className="search-sources"><span>함께 검색 중</span><b className={`source green ${!query ? 'muted-source' : ''}`}><i />문장</b>{photo && <b className="source peach"><i />사진</b>}<b className="source sage"><i />조건</b></div>
    </div>
  </section>
}
