'use client'

import { createContext, useContext, useState, type ReactNode } from 'react'
import type { ViewMode } from '@/types/view-mode'

const ViewModeContext = createContext<{ mode: ViewMode; setMode: (mode: ViewMode) => void }>({ mode: 'app', setMode: () => {} })

/** 웹/앱 미리보기 모드. 화면 컴포넌트는 이 값으로 web/ 또는 mobile/ 구현을 고릅니다. */
export function ViewModeProvider({ children }: { children: ReactNode }) {
  const [mode, setMode] = useState<ViewMode>('app')
  return <ViewModeContext.Provider value={{ mode, setMode }}>{children}</ViewModeContext.Provider>
}

export const useViewMode = () => useContext(ViewModeContext)
