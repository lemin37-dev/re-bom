'use client'

import { AnimalPlaceMap } from '@/screens/common/AnimalPlaceMap'
import type { Animal } from '@/types/animal'

/** 웹 검색 결과 지도 보기: 불러온 아이들을 실제 보호센터 위치에 (screens/common/AnimalPlaceMap) */
export function ResultMap({ animals, total, onOpen }: { animals: Animal[]; total: number; onOpen: (a: Animal) => void }) {
  return <AnimalPlaceMap animals={animals} total={total} onOpen={onOpen} />
}
