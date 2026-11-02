import { Icon } from '@/components/icons/Icon'
import { animalMeta, animalTitle, availabilityLabel, isAvailable, statusLabel } from '@/lib/animal'
import type { Animal } from '@/types/animal'
import { AnimalPhoto } from './AnimalPhoto'
import { Reasons } from './Reasons'

/** 그리드형 동물 카드 (웹 검색 결과, 상세의 비슷한 아이들) */
export function PetCard({ animal: a, onOpen }: { animal: Animal; onOpen: (a: Animal) => void }) {
  const available = isAvailable(a)
  return <button className="animal-card" onClick={() => onOpen(a)}>
    <div className="animal-visual" style={{ background: a.bg }}><AnimalPhoto animal={a} /><span className={`status-badge ${available ? '' : 'pending'}`}>{statusLabel(a)}</span>{a.score !== undefined && <span className="match-badge">매칭 <strong>{a.score}%</strong></span>}</div>
    <div className="card-body"><h3>{animalTitle(a)}</h3><p className="animal-meta">{animalMeta(a)}</p>{!!a.reasons?.length && <><span className="reason-label">매칭 이유</span><Reasons animal={a} /></>}{a.quote && <blockquote>“{a.quote}”</blockquote>}
      <div className="card-bottom"><p><Icon name="pin" size={14} />{a.center}{a.distance !== undefined && <b>· {a.distance}km</b>}</p><p className={available ? 'available-date' : 'pending-date'}><Icon name="calendar" size={14} />{availabilityLabel(a)}</p></div>
    </div>
  </button>
}
