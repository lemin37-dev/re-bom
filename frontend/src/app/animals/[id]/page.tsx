import type { Metadata } from 'next'
import { AnimalDetailScreen } from '@/screens/animal-detail/AnimalDetailScreen'

type Params = { params: Promise<{ id: string }> }

export const metadata: Metadata = { title: '동물 상세 | RE:봄' }

/** /animals/:구조번호. 공고 정보는 화면에서 검색 API로 불러옵니다. */
export default async function AnimalPage({ params }: Params) {
  const { id } = await params
  return <AnimalDetailScreen id={decodeURIComponent(id)} />
}
