'use client'

import { AnimalArt } from '@/components/animal/AnimalArt'
import { Icon } from '@/components/icons/Icon'
import type { Animal } from '@/types/animal'

/** 상세 상단 갤러리: 큰 그림 + 다른 모습 썸네일 2개 (view 0은 기본) */
export function PetGallery({ animal: a, view, onView }: { animal: Animal; view: number; onView: (view: number) => void }) {
  return <div className="pet-gallery">
    <div className={`gallery-main gallery-view-${view}`} style={{ background: a.bg }}><AnimalArt animal={a} /><span className="gallery-count">{view + 1} / 3</span><div className="gallery-caption"><Icon name="image" size={14} />실제 사진 대신 표현한 예시 일러스트</div></div>
    <div className="gallery-thumbs">{[1, 2].map((thumb, i) => <button key={thumb} className={`gallery-thumb gallery-view-${thumb} ${view === thumb ? 'chosen' : ''}`} style={{ background: i === 0 ? '#E7ECDC' : a.bg }} onClick={() => onView(view === thumb ? 0 : thumb)} aria-label={`${i === 0 ? '얼굴' : '전체 모습'} 일러스트 보기`} aria-pressed={view === thumb}><AnimalArt animal={a} /><span>{i === 0 ? '가까이 보기' : '다른 모습 보기'}</span></button>)}</div>
  </div>
}
