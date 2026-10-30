'use client'

import { ROUTES } from '@/constants/routes'
import { useExperience } from '@/providers/ExperienceProvider'
import { useViewMode } from '@/providers/ViewModeProvider'
import type { Animal } from '@/types/animal'
import { AnimalDetail } from './AnimalDetail'

/** /animals/[id] 화면: 현재 모드와 화면 이동 동작을 AnimalDetail에 연결합니다. */
export function AnimalDetailScreen({ animal }: { animal: Animal }) {
  const { mode } = useViewMode()
  const { actionTarget, backToResults, navigate, openAnimal } = useExperience()
  // 다른 아이로 이동하면 갤러리·모달 상태를 새로 시작하도록 key를 둡니다.
  return <AnimalDetail key={animal.id} animal={animal} compact={mode === 'app'} actionTarget={actionTarget} onBack={backToResults} onGuide={() => navigate(ROUTES.guide)} onOpen={openAnimal} />
}
