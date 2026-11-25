'use client'

import { usePathname } from 'next/navigation'
import { ROUTES } from '@/constants/routes'
import { useExperience } from '@/providers/ExperienceProvider'

const TABS = [
  { href: ROUTES.guide, number: '01', label: '입양 절차 안내' },
  { href: ROUTES.guideSupport, number: '02', label: '우리 지역 입양 혜택' },
  { href: ROUTES.guideEvents, number: '03', label: '전국 입양 행사' },
] as const

/** 입양 안내 3개 페이지를 오가는 탭 */
export function GuideTabs() {
  const pathname = usePathname()
  const { navigate } = useExperience()
  return <nav className="guide-tabs" aria-label="입양 안내">{TABS.map(t => <a key={t.href} href={t.href} aria-current={pathname === t.href ? 'page' : undefined} className={pathname === t.href ? 'active' : ''} onClick={e => { e.preventDefault(); navigate(t.href) }}><b>{t.number}</b>{t.label}</a>)}</nav>
}
