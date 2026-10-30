'use client'

import { createContext, useContext, useRef, useState, type ReactNode, type RefObject } from 'react'
import { usePathname, useRouter } from 'next/navigation'
import { ROUTES } from '@/constants/routes'
import { useScrollMemory } from '@/hooks/useScrollMemory'
import type { Animal } from '@/types/animal'
import type { ViewMode } from '@/types/view-mode'
import { useSearch } from './SearchProvider'
import { useViewMode } from './ViewModeProvider'

type Experience = {
  /** 앱 모드에서 스크롤되는 본문 영역 */
  scrollRef: RefObject<HTMLDivElement | null>
  /** 앱 모드 상세 화면의 하단 고정 버튼이 포털로 들어갈 자리 */
  actionTarget: HTMLDivElement | null
  setActionTarget: (el: HTMLDivElement | null) => void
  mascotOpen: boolean
  setMascotOpen: (open: boolean) => void
  /** 다른 화면으로 이동 (맨 위에서 시작) */
  navigate: (href: string) => void
  openAnimal: (animal: Animal) => void
  /** 검색 결과로 돌아가기 (이전 스크롤 위치 복원) */
  backToResults: () => void
  switchMode: (mode: ViewMode) => void
}

const ExperienceContext = createContext<Experience | null>(null)

/** 화면 이동·스크롤 복원·웹/앱 전환처럼 화면 전체에 걸친 동작을 제공합니다. */
export function ExperienceProvider({ children }: { children: ReactNode }) {
  const router = useRouter()
  const pathname = usePathname()
  const { mode, setMode } = useViewMode()
  const { closeFilters } = useSearch()
  const scrollRef = useRef<HTMLDivElement>(null)
  const [actionTarget, setActionTarget] = useState<HTMLDivElement | null>(null)
  const [mascotOpen, setMascotOpen] = useState(false)
  const { nextScroll, scrollTo } = useScrollMemory(mode, pathname, scrollRef)

  function go(href: string, top: number | null) {
    if (href === pathname) {
      if (top !== null) requestAnimationFrame(() => scrollTo(top))
      return
    }
    nextScroll(top)
    router.push(href, { scroll: false })
  }

  const value: Experience = {
    scrollRef, actionTarget, setActionTarget, mascotOpen, setMascotOpen,
    navigate: href => go(href, 0),
    openAnimal: animal => go(ROUTES.animal(animal.id), 0),
    backToResults: () => go(ROUTES.search, null),
    switchMode: next => { if (next === mode) return; closeFilters(); setMode(next) },
  }
  return <ExperienceContext.Provider value={value}>{children}</ExperienceContext.Provider>
}

export function useExperience() {
  const context = useContext(ExperienceContext)
  if (!context) throw new Error('useExperience는 ExperienceProvider 안에서만 사용할 수 있어요.')
  return context
}
