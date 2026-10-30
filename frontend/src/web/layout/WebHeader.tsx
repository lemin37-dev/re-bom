'use client'

import type { ReactNode } from 'react'
import { NAV_ITEMS } from '@/constants/routes'

/** 웹 상단 헤더: 로고 · 주 메뉴 · 테마 토글 */
export function WebHeader({ activeHref, onNavigate, onHome, themeControl }: { activeHref: string; onNavigate: (href: string) => void; onHome: () => void; themeControl: ReactNode }) {
  return <header className="site-header"><div className="header-inner">
    <button className="brand" onClick={onHome} aria-label="다시 봄 첫 화면"><span className="logo">RE:<span>봄</span><i /></span><span className="brand-caption">유기동물에게 새로운 인연을</span></button>
    <nav aria-label="주 메뉴">{NAV_ITEMS.map(item => <button className={`nav-item ${activeHref === item.href ? 'active' : ''}`} key={item.href} onClick={() => onNavigate(item.href)}>{item.title}</button>)}</nav>
    {themeControl}
  </div></header>
}
