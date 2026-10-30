'use client'

import type { ReactNode } from 'react'
import { Icon } from '@/components/icons/Icon'

/** 앱 상단 헤더: 목록에서는 로고, 상세에서는 뒤로 가기 */
export function MobileHeader({ title, detail, onBack, onHome, themeControl }: { title: string; detail: boolean; onBack: () => void; onHome: () => void; themeControl: ReactNode }) {
  return <header className="mobile-header">
    {detail
      ? <><button className="mobile-back" onClick={onBack} aria-label="검색 결과로 돌아가기"><Icon name="arrow" size={21} /></button><strong>친구 알아보기</strong></>
      : <button className="brand" onClick={onHome} aria-label="다시 봄 입양 찾기"><span className="logo">RE:<span>봄</span><i /></span></button>}
    {themeControl}
    <span className="sr-only">{title}</span>
  </header>
}
