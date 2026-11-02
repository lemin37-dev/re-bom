'use client'

import type { ReactNode } from 'react'
import { usePathname } from 'next/navigation'
import { MascotDialog } from '@/components/mascot/MascotDialog'
import { ThemeToggle } from '@/components/mascot/ThemeToggle'
import { FilterDialog } from '@/components/search/FilterDialog'
import { activeNavHref, isAnimalDetail, navTitle, ROUTES } from '@/constants/routes'
import { MobileHeader } from '@/mobile/layout/MobileHeader'
import { MobileTabs } from '@/mobile/layout/MobileTabs'
import { useExperience } from '@/providers/ExperienceProvider'
import { useMascot } from '@/providers/MascotProvider'
import { useSearch } from '@/providers/SearchProvider'
import { useViewMode } from '@/providers/ViewModeProvider'
import { WebHeader } from '@/web/layout/WebHeader'
import { SiteFooter } from './SiteFooter'

/**
 * 모든 화면을 감싸는 틀.
 * 웹 모드(넓은 화면)는 WebHeader, 앱 모드(좁은 화면·하이브리드 앱)는 MobileHeader · MobileTabs 를 붙이고
 * 본문(children)과 푸터·다이얼로그는 공통으로 사용합니다.
 */
export function ExperienceShell({ children }: { children: ReactNode }) {
  const pathname = usePathname()
  const { theme } = useMascot()
  const { mode } = useViewMode()
  const { setSearched } = useSearch()
  const { scrollRef, setActionTarget, mascotOpen, setMascotOpen, navigate, backToResults } = useExperience()
  const isApp = mode === 'app'
  const detail = isAnimalDetail(pathname)
  const activeHref = activeNavHref(pathname)

  return <div className={`app ${isApp ? 'app-preview-mode' : 'web-preview-mode'}`} data-mascot={theme}>
    <div className="experience-layout">
      <div className={isApp ? 'mobile-app' : 'web-experience'}>
        {isApp
          ? <MobileHeader title={navTitle(activeHref)} detail={detail} onBack={backToResults} onHome={() => navigate(ROUTES.search)} themeControl={<ThemeToggle />} />
          : <WebHeader activeHref={activeHref} onNavigate={navigate} onHome={() => { navigate(ROUTES.search); setSearched(false) }} themeControl={<ThemeToggle />} />}
        <div className="experience-scroll" ref={scrollRef}>
          <main className="main">{children}</main>
          <SiteFooter onMascot={() => setMascotOpen(true)} />
        </div>
        <div ref={setActionTarget} className={isApp && detail ? 'mobile-action-slot' : 'inactive-action-slot'} />
        {isApp && !detail && <MobileTabs activeHref={activeHref} onNavigate={navigate} onMascot={() => setMascotOpen(true)} />}
      </div>
    </div>
    <FilterDialog sheet={isApp} />
    <MascotDialog open={mascotOpen} onClose={() => setMascotOpen(false)} />
  </div>
}
