'use client'

import { ROUTES } from '@/constants/routes'
import { useExperience } from '@/providers/ExperienceProvider'
import { useSearch } from '@/providers/SearchProvider'
import { useViewMode } from '@/providers/ViewModeProvider'
import { ShelterDirectory } from './ShelterDirectory'

export function ShelterDirectoryScreen() {
  const { mode } = useViewMode()
  const { focusCenter } = useSearch()
  const { navigate } = useExperience()
  return <ShelterDirectory compact={mode === 'app'} onFind={center => { focusCenter(center); navigate(ROUTES.search) }} />
}
