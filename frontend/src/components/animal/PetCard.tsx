import { Icon } from '@/components/icons/Icon'
import { availabilityLabel, statusLabel } from '@/lib/animal'
import type { Animal } from '@/types/animal'
import { AnimalArt } from './AnimalArt'
import { Reasons } from './Reasons'

/** 그리드형 동물 카드 (웹 검색 결과, 상세의 비슷한 아이들) */
export function PetCard({ animal: a, onOpen }: { animal: Animal; onOpen: (a: Animal) => void }) {
  return <button className="animal-card" onClick={() => onOpen(a)}>
    <div className="animal-visual" style={{ background: a.bg }}><AnimalArt animal={a} /><span className={`status-badge ${a.available ? '' : 'pending'}`}>{statusLabel(a)}</span><span className="match-badge">매칭 <strong>{a.score}%</strong></span></div>
    <div className="card-body"><h3>{a.kind} · {a.gender} · {a.age}살</h3><p className="animal-meta">{a.kg}kg · {a.color} · {a.care}</p><span className="reason-label">매칭 이유</span><Reasons animal={a} /><blockquote>“{a.quote}”</blockquote>
      <div className="card-bottom"><p><Icon name="pin" size={14} />{a.center}<b>· {a.distance}km</b></p><p className={a.available ? 'available-date' : 'pending-date'}><Icon name="calendar" size={14} />{availabilityLabel(a)}</p></div>
    </div>
  </button>
}
