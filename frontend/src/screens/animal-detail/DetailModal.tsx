'use client'

import { useEffect, useRef } from 'react'
import { Icon } from '@/components/icons/Icon'
import type { Shelter } from '@/types/animal'
import { Checklist } from '../common/Checklist'

export type DetailModalKind = 'contact' | 'checklist'

/** 상세 화면 모달: 보호소 문의 안내 또는 방문 전 체크리스트 */
export function DetailModal({ kind, center, noticeNumber, checked, onChecked, onClose }: { kind: DetailModalKind | null; center: Shelter; noticeNumber: string; checked: string[]; onChecked: (value: string[]) => void; onClose: () => void }) {
  const modalRef = useRef<HTMLDialogElement>(null)
  useEffect(() => { if (kind) modalRef.current?.showModal(); else modalRef.current?.close() }, [kind])

  return <dialog ref={modalRef} className="detail-modal" aria-labelledby="detail-modal-title" onCancel={onClose} onClick={e => { if (e.target === modalRef.current) onClose() }}>
    <button className="modal-close icon-button" onClick={onClose} aria-label="닫기"><Icon name="close" /></button>
    <div className="eyebrow">첫 만남을 준비하는 마음</div>
    <h2 id="detail-modal-title">{kind === 'contact' ? '보호소에 먼저 이야기해 주세요' : '방문 전 체크리스트'}</h2>
    {kind === 'contact'
      ? <>
          <p className="modal-description">아이의 공고번호를 알려주면 더 편하게 상담할 수 있어요.</p>
          <div className="contact-preview"><strong>{center.name}</strong><span>공고번호 {noticeNumber}</span><b>{center.phone}</b><small>예시 번호이므로 전화 연결은 제공하지 않아요.</small></div>
          <ol className="contact-prompts"><li>현재 입양이 가능한지 확인해 주세요.</li><li>아이의 건강과 생활 습관을 물어보세요.</li><li>방문 가능 시간과 필요한 준비물을 확인해 주세요.</li></ol>
          <div className="prototype-notice">가상 보호소 정보이며, 실제 문의·예약은 접수되지 않아요.</div>
        </>
      : <Checklist checked={checked} onChange={onChecked} />}
    <button className="primary-button full-width" onClick={onClose}>확인했어요<Icon name="check" size={17} /></button>
  </dialog>
}
