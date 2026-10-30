'use client'

import { useEffect, useRef, useState } from 'react'
import { Icon } from '@/components/icons/Icon'
import { allFilters, filterLabels, filterOptions } from '@/data/filters'
import { useSearch } from '@/providers/SearchProvider'
import type { FilterKey, Filters } from '@/types/filters'

/** 생활 조건 선택 창. 웹은 가운데 모달, 앱은 바텀시트(sheet)로 표시됩니다. */
export function FilterDialog({ sheet }: { sheet: boolean }) {
  const { filters, filterOpen, closeFilters, applyFilters } = useSearch()
  const [draft, setDraft] = useState<Filters>(filters)
  const dialogRef = useRef<HTMLDialogElement>(null)
  // 창을 열 때의 조건으로 초안을 채웁니다.
  const [wasOpen, setWasOpen] = useState(filterOpen)
  if (filterOpen !== wasOpen) {
    setWasOpen(filterOpen)
    if (filterOpen) setDraft(filters)
  }
  useEffect(() => { if (filterOpen) dialogRef.current?.showModal(); else dialogRef.current?.close() }, [filterOpen])

  return <dialog ref={dialogRef} className={`filter-dialog ${sheet ? 'app-filter-sheet' : ''}`} aria-label="생활 조건 선택" onCancel={closeFilters} onClick={e => { if (e.target === dialogRef.current) closeFilters() }}>
    <div className="dialog-header"><div><span className="eyebrow">나의 일상에 맞춰서</span><h2>어떤 친구를 찾고 있나요?</h2></div><button className="icon-button" onClick={closeFilters} aria-label="조건 창 닫기"><Icon name="close" /></button></div>
    <p className="dialog-description">꼭 필요한 조건만 골라도 괜찮아요.</p>
    {(Object.keys(draft) as FilterKey[]).map(key => <fieldset key={key}><legend>{filterLabels[key]}</legend><div className="option-buttons">{filterOptions[key].map(option => <button className={draft[key] === option ? 'selected' : ''} key={option} onClick={() => setDraft({ ...draft, [key]: option })}>{option}{draft[key] === option && <Icon name="check" size={13} />}</button>)}</div></fieldset>)}
    <div className="dialog-actions"><button className="reset-button" onClick={() => setDraft(allFilters)}>조건 초기화</button><button className="primary-button" onClick={() => applyFilters(draft)}>선택한 조건 적용<Icon name="check" size={17} /></button></div>
  </dialog>
}
