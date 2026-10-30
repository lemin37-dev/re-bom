'use client'

import { Icon } from '@/components/icons/Icon'
import { visitChecklistItems } from '@/data/adoption-guide'

/** 방문 전 체크리스트 (입양 안내 화면, 상세 화면 모달에서 사용) */
export function Checklist({ checked, onChange }: { checked: string[]; onChange: (value: string[]) => void }) {
  const toggle = (item: string) => onChange(checked.includes(item) ? checked.filter(x => x !== item) : [...checked, item])
  return <div className="checklist">
    <div className="checklist-intro"><span className="soft-icon"><Icon name="calendar" size={24} /></span><div><h3>설레는 첫 만남, 준비는 천천히</h3><p>빠짐없이 준비하면 아이도, 나도 더 편안해져요.</p></div><span className="check-progress">{checked.length} / {visitChecklistItems.length}</span></div>
    <div className="checklist-items">{visitChecklistItems.map(item => <label key={item} className={checked.includes(item) ? 'is-checked' : ''}><input type="checkbox" checked={checked.includes(item)} onChange={() => toggle(item)} /><span>{item}</span></label>)}</div>
    <p className="reference-note">{checked.length === visitChecklistItems.length ? '준비를 모두 확인했어요. 방문 전 보호센터에 한 번 더 연락해 주세요.' : '준비물은 센터마다 다를 수 있어요. 방문 전 전화로 확인해 주세요.'}</p>
  </div>
}
