import type { Animal } from '@/types/animal'

/** 매칭 이유 칩 (출처별 색상: 문장/사진/조건) */
export function Reasons({ animal }: { animal: Animal }) {
  return <div className="reason-chips">{animal.reasons.map(([label, source]) => <span key={label} className={`reason-chip ${source === '사진' ? 'photo-reason' : source === '조건' ? 'condition-reason' : ''}`}><i />{label}<small>{source}</small></span>)}</div>
}
