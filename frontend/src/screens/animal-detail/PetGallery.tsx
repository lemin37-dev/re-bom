'use client'

import { useRef } from 'react'
import { AnimalPhoto } from '@/components/animal/AnimalPhoto'
import { Icon } from '@/components/icons/Icon'
import type { Animal } from '@/types/animal'

/**
 * 상세 상단 갤러리: 좌우로 넘기는 큰 사진 + 공고 사진 썸네일. 사진이 없으면 일러스트 한 장.
 * 현재 사진 번호(view)는 스크롤 위치에서만 정해지고, 썸네일·화살표는 스크롤만 시킵니다.
 */
export function PetGallery({ animal: a, view, onView }: { animal: Animal; view: number; onView: (view: number) => void }) {
  const track = useRef<HTMLDivElement>(null)
  const count = a.photos.length
  const slides = Math.max(count, 1)

  function goTo(i: number) {
    const el = track.current
    if (el) el.scrollTo({ left: i * el.clientWidth, behavior: 'smooth' })
  }
  function onScroll() {
    const el = track.current
    if (!el || !el.clientWidth) return
    const next = Math.min(slides - 1, Math.max(0, Math.round(el.scrollLeft / el.clientWidth)))
    if (next !== view) onView(next)
  }

  return <div className="pet-gallery">
    <div className="gallery-main" style={{ background: a.bg }}>
      <div className="gallery-track" ref={track} onScroll={onScroll} tabIndex={count > 1 ? 0 : undefined} aria-label={count > 1 ? '공고 사진, 좌우로 넘겨 보세요' : undefined}>
        {Array.from({ length: slides }, (_, i) => <div className="gallery-slide" key={i}><AnimalPhoto animal={a} index={i} /></div>)}
      </div>
      {count > 1 && <>
        <button className="gallery-nav prev" onClick={() => goTo(view - 1)} disabled={view === 0} aria-label="이전 사진"><Icon name="arrow" size={16} /></button>
        <button className="gallery-nav next" onClick={() => goTo(view + 1)} disabled={view === count - 1} aria-label="다음 사진"><Icon name="arrow" size={16} /></button>
      </>}
      {count > 0 && <span className="gallery-count">{view + 1} / {count}</span>}
      <div className="gallery-caption"><Icon name="image" size={14} />{count ? '보호소가 올린 공고 사진' : '사진이 없는 공고라 일러스트로 보여 드려요'}</div>
    </div>
    {count > 1 && <div className="gallery-thumbs photo-thumbs">{a.photos.map((_, i) => <button key={i} className={`gallery-thumb ${view === i ? 'chosen' : ''}`} style={{ background: a.bg }} onClick={() => goTo(i)} aria-label={`사진 ${i + 1} 보기`} aria-pressed={view === i}><AnimalPhoto animal={a} index={i} small /></button>)}</div>}
  </div>
}
