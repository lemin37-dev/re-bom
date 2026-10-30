'use client'

import { MobileSearchScreen } from '@/mobile/screens/MobileSearchScreen'
import { useViewMode } from '@/providers/ViewModeProvider'
import { WebSearchScreen } from '@/web/screens/WebSearchScreen'

/** 입양 찾기(/) — 화면 배치가 크게 달라 웹과 앱 구현을 따로 둡니다. 검색 상태는 SearchProvider로 공유합니다. */
export function SearchScreen() {
  const { mode } = useViewMode()
  return mode === 'app' ? <MobileSearchScreen /> : <WebSearchScreen />
}
