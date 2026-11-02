'use client'

import { AnimalPhoto } from '@/components/animal/AnimalPhoto'
import { Reasons } from '@/components/animal/Reasons'
import { Icon } from '@/components/icons/Icon'
import { animalMeta, animalTitle, availabilityLabel, isAvailable, statusLabel } from '@/lib/animal'
import type { Animal } from '@/types/animal'

/** 앱 세로형 동물 카드. featured는 목록 첫 카드 강조 */
export function MobilePetCard({ animal: a, onOpen, featured = false }: { animal: Animal; onOpen: (a: Animal) => void; featured?: boolean }) {
  const available = isAvailable(a)
  return <button className={`mobile-pet-card ${featured ? 'featured-pet' : ''}`} onClick={() => onOpen(a)}>
    <div className="mobile-pet-visual" style={{ background: a.bg }}><AnimalPhoto animal={a} /><span className={`status-badge ${available ? '' : 'pending'}`}>{statusLabel(a)}</span>{featured && <span className="mobile-pet-greeting">우리, 만나 볼까요?</span>}</div>
    <div className="mobile-pet-copy">
      <div className="mobile-pet-title"><h3>{animalTitle(a)}</h3>{a.score !== undefined && <span>매칭 <b>{a.score}%</b></span>}</div>
      <p className="mobile-pet-meta">{animalMeta(a)}</p>
      {!!a.reasons?.length && <div className="mobile-pet-reasons"><span>잘 맞는 이유</span><Reasons animal={a} /></div>}
      {a.quote && <blockquote>“{a.quote}”</blockquote>}
      <div className="mobile-pet-center"><Icon name="pin" size={14} /><span>{a.center}</span>{a.distance !== undefined && <b>{a.distance}km</b>}</div>
      <p className={`mobile-pet-date ${available ? '' : 'pending-date'}`}><Icon name="calendar" size={14} />{availabilityLabel(a)}<Icon name="arrow" size={16} /></p>
    </div>
  </button>
}
