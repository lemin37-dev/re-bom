'use client'

import type { ReactNode } from 'react'
import { usePathname } from 'next/navigation'
import { MascotDialog } from '@/components/mascot/MascotDialog'
import { ThemeToggle } from '@/components/mascot/ThemeToggle'
import { FilterDialog } from '@/components/search/FilterDialog'
import { activeNavHref, isAnimalDetail, navTitle, ROUTES } from '@/constants/routes'
import { AppPreviewAside } from '@/mobile/layout/AppPreviewAside'
import { MobileHeader } from '@/mobile/layout/MobileHeader'
import { MobileTabs } from '@/mobile/layout/MobileTabs'
import { useExperience } from '@/providers/ExperienceProvider'
import { useMascot } from '@/providers/MascotProvider'
import { useSearch } from '@/providers/SearchProvider'
import { useViewMode } from '@/providers/ViewModeProvider'
import { WebHeader } from '@/web/layout/WebHeader'
import { PreviewControls } from './PreviewControls'
import { SiteFooter } from './SiteFooter'

/**
 * 모든 화면을 감싸는 틀.
 * 웹 모드는 WebHeader, 앱 모드는 모바일 프레임(MobileHeader · MobileTabs)을 붙이고
 * 본문(children)과 푸터·다이얼로그는 공통으로 사용합니다.
 */
export function ExperienceShell({ children }: { children: ReactNode }) {
  const pathname = usePathname()
  const { theme } = useMascot()
  const { mode } = useViewMode()
  const { setSearched } = useSearch()
  const { scrollRef, setActionTarget, mascotOpen, setMascotOpen, navigate, backToResults, switchMode } = useExperience()
  const isApp = mode === 'app'
  const detail = isAnimalDetail(pathname)
  const activeHref = activeNavHref(pathname)

  return <div className={`app ${isApp ? 'app-preview-mode' : 'web-preview-mode'}`} data-mascot={theme}>
    <PreviewControls mode={mode} onChange={switchMode} />
    <div className="experience-layout">
      {isApp && <AppPreviewAside activeHref={activeHref} detail={detail} onNavigate={navigate} />}
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
