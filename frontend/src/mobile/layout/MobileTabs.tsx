'use client'

import { Icon } from '@/components/icons/Icon'
import { MascotArt } from '@/components/mascot/MascotArt'
import { NAV_ITEMS } from '@/constants/routes'

/** 앱 하단 탭 메뉴 (주 메뉴 4개 + 마스코트) */
export function MobileTabs({ activeHref, onNavigate, onMascot }: { activeHref: string; onNavigate: (href: string) => void; onMascot: () => void }) {
  return <nav className="mobile-tabs" aria-label="앱 하단 메뉴">
    {NAV_ITEMS.map(item => <button key={item.href} aria-current={activeHref === item.href ? 'page' : undefined} className={activeHref === item.href ? 'active' : ''} onClick={() => onNavigate(item.href)}><span><Icon name={item.icon} size={21} /></span><strong>{item.tabLabel}</strong></button>)}
    <button onClick={onMascot} aria-haspopup="dialog"><span><MascotArt className="mascot-tab-art" /></span><strong>마스코트</strong></button>
  </nav>
}
