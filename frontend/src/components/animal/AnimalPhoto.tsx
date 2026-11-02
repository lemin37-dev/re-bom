'use client'

import { useState } from 'react'
import type { Animal } from '@/types/animal'
import { AnimalArt } from './AnimalArt'

/**
 * 공고 사진. 사진이 없거나 열리지 않으면(2021-11 이전 공고 등) 일러스트로 대신합니다.
 * 사진 주소가 공공 API의 http:// 주소라 next/image 대신 img를 씁니다.
 */
export function AnimalPhoto({ animal: a, index = 0, small = false }: { animal: Animal; index?: number; small?: boolean }) {
  const src = a.photos[index]
  const [failed, setFailed] = useState<string | null>(null)
  if (!src || failed === src) return <AnimalArt animal={a} small={small} />
  // eslint-disable-next-line @next/next/no-img-element
  return <img className="animal-photo" src={src} alt={`${a.color} ${a.kind} 사진${a.photos.length > 1 ? ` ${index + 1}` : ''}`} loading="lazy" referrerPolicy="no-referrer" onError={() => setFailed(src)} />
}
