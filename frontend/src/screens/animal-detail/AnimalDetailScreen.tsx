'use client'

import { useEffect, useState } from 'react'
import { MascotState } from '@/components/mascot/MascotState'
import { ROUTES } from '@/constants/routes'
import { animalTitle } from '@/lib/animal'
import { ApiError, fetchAnimal, fetchAnimals } from '@/lib/api'
import { useExperience } from '@/providers/ExperienceProvider'
import { useViewMode } from '@/providers/ViewModeProvider'
import type { Animal } from '@/types/animal'
import { AnimalDetail } from './AnimalDetail'

type Loaded = { id: string; animal: Animal | null; similar: Animal[]; error: string | null; notFound: boolean }

/** /animals/[id] 화면: 공고를 불러와 현재 모드와 화면 이동 동작을 AnimalDetail에 연결합니다. */
export function AnimalDetailScreen({ id }: { id: string }) {
  const { mode } = useViewMode()
  const { actionTarget, backToResults, navigate, openAnimal } = useExperience()
  const [loaded, setLoaded] = useState<Loaded | null>(null)
  const [retry, setRetry] = useState(0)

  useEffect(() => {
    const controller = new AbortController()
    const { signal } = controller
    fetchAnimal(id, signal)
      .then(async animal => {
        document.title = `${animalTitle(animal)} | RE:봄`
        // 비슷한 아이들: 같은 종의 보호 중인 공고 (자기 자신 제외)
        const similar = await fetchAnimals({ species: animal.species, state: 'protect', size: 4 }, signal)
          .then(r => r.items.filter(p => p.id !== animal.id).slice(0, 3))
          .catch(() => [])
        if (!signal.aborted) setLoaded({ id, animal, similar, error: null, notFound: false })
      })
      .catch(e => {
        if (signal.aborted) return
        const notFound = e instanceof ApiError && e.status === 404
        setLoaded({ id, animal: null, similar: [], error: e instanceof ApiError ? e.message : '공고를 불러오지 못했어요.', notFound })
      })
    return () => controller.abort()
  }, [id, retry])

  if (!loaded || loaded.id !== id) return <section className="expanded-detail"><MascotState kind="loading" /></section>
  if (!loaded.animal) {
    return <section className="expanded-detail">
      <MascotState kind={loaded.notFound ? 'not-found' : 'error'} detail={loaded.notFound ? null : loaded.error} serverHint={!loaded.notFound && !!loaded.error?.includes('연결')}>
        {loaded.notFound
          ? <button className="primary-button" onClick={backToResults}>입양 찾기로 돌아가기</button>
          : <button className="primary-button" onClick={() => setRetry(n => n + 1)}>다시 불러오기</button>}
      </MascotState>
    </section>
  }
  // 다른 아이로 이동하면 갤러리·모달 상태를 새로 시작하도록 key를 둡니다.
  return <AnimalDetail key={loaded.animal.id} animal={loaded.animal} similar={loaded.similar} compact={mode === 'app'} actionTarget={actionTarget} onBack={backToResults} onGuide={() => navigate(ROUTES.guide)} onOpen={openAnimal} />
}
