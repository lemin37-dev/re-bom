'use client'

import type { ReactNode } from 'react'
import { ExperienceProvider } from './ExperienceProvider'
import { MascotProvider } from './MascotProvider'
import { SearchProvider } from './SearchProvider'
import { ViewModeProvider } from './ViewModeProvider'

export function AppProviders({ children }: { children: ReactNode }) {
  return <MascotProvider>
    <ViewModeProvider>
      <SearchProvider>
        <ExperienceProvider>{children}</ExperienceProvider>
      </SearchProvider>
    </ViewModeProvider>
  </MascotProvider>
}
