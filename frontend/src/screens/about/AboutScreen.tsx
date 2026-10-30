'use client'

import { ROUTES } from '@/constants/routes'
import { useExperience } from '@/providers/ExperienceProvider'
import { useSearch } from '@/providers/SearchProvider'
import { AboutPage } from './AboutPage'

export function AboutScreen() {
  const { setCenterFilter } = useSearch()
  const { navigate } = useExperience()
  return <AboutPage onFind={() => { setCenterFilter(null); navigate(ROUTES.search) }} />
}
