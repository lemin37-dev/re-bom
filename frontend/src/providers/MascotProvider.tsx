'use client'

import { createContext, useContext, useState, type ReactNode } from 'react'
import type { MascotTheme } from '@/data/mascots'

const MascotContext = createContext<{ theme: MascotTheme; setTheme: (theme: MascotTheme) => void }>({ theme: 'remeong', setTheme: () => {} })

/** 화면 분위기(리멍이/봄냥이) 테마. 검색 조건과는 독립적입니다. */
export function MascotProvider({ children }: { children: ReactNode }) {
  const [theme, setTheme] = useState<MascotTheme>('remeong')
  return <MascotContext.Provider value={{ theme, setTheme }}>{children}</MascotContext.Provider>
}

export const useMascot = () => useContext(MascotContext)
