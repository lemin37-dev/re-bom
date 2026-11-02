'use client'

import { AnimalPhoto } from '@/components/animal/AnimalPhoto'
import { Icon } from '@/components/icons/Icon'
import type { Animal } from '@/types/animal'

/** 상세 상단 갤러리: 큰 사진 + 공고 사진 썸네일. 사진이 없으면 일러스트 한 장 */
export function PetGallery({ animal: a, view, onView }: { animal: Animal; view: number; onView: (view: number) => void }) {
  const count = a.photos.length
  return <div className="pet-gallery">
    <div className="gallery-main" style={{ background: a.bg }}>
      <AnimalPhoto key={view} animal={a} index={view} />
      {count > 0 && <span className="gallery-count">{view + 1} / {count}</span>}
      <div className="gallery-caption"><Icon name="image" size={14} />{count ? '보호소가 올린 공고 사진' : '사진이 없는 공고라 일러스트로 보여 드려요'}</div>
    </div>
    {count > 1 && <div className="gallery-thumbs photo-thumbs">{a.photos.map((_, i) => <button key={i} className={`gallery-thumb ${view === i ? 'chosen' : ''}`} style={{ background: a.bg }} onClick={() => onView(i)} aria-label={`사진 ${i + 1} 보기`} aria-pressed={view === i}><AnimalPhoto animal={a} index={i} small /></button>)}</div>}
  </div>
}
