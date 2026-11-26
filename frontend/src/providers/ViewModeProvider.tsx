'use client'

import { createContext, useContext, useSyncExternalStore, type ReactNode } from 'react'
import type { ViewMode } from '@/types/view-mode'

/** 이 폭 이상이면 웹 화면, 미만이면 앱 화면 */
export const WEB_MIN_WIDTH = 768
const QUERY = `(min-width: ${WEB_MIN_WIDTH}px)`

/**
 * NEXT_PUBLIC_VIEW_MODE 로 고정할 수 있습니다.
 * - auto(기본): 화면 폭으로 고름
 * - app: 하이브리드 앱(웹뷰) 빌드처럼 항상 앱 화면
 * - web: 항상 웹 화면
 */
const FORCED = process.env.NEXT_PUBLIC_VIEW_MODE === 'app' || process.env.NEXT_PUBLIC_VIEW_MODE === 'web' ? process.env.NEXT_PUBLIC_VIEW_MODE : null

function subscribe(onChange: () => void) {
  const media = window.matchMedia(QUERY)
  media.addEventListener('change', onChange)
  return () => media.removeEventListener('change', onChange)
}

const getSnapshot = (): ViewMode => (window.matchMedia(QUERY).matches ? 'web' : 'app')
// 서버 렌더에서는 폭을 모르므로 앱 화면(모바일 우선)으로 그리고, 브라우저에서 바로 맞춥니다.
const getServerSnapshot = (): ViewMode => 'app'

const ViewModeContext = createContext<{ mode: ViewMode }>({ mode: 'app' })

/** 웹/앱 화면 모드. 화면 컴포넌트는 이 값으로 web/ 또는 mobile/ 구현을 고릅니다. */
export function ViewModeProvider({ children }: { children: ReactNode }) {
  const detected = useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot)
  const mode = (FORCED as ViewMode | null) ?? detected
  return <ViewModeContext.Provider value={{ mode }}>{children}</ViewModeContext.Provider>
}

export const useViewMode = () => useContext(ViewModeContext)
