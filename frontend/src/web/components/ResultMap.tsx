'use client'

import { SearchPlaceMap } from '@/screens/common/SearchPlaceMap'
import type { Animal } from '@/types/animal'

/** 웹 검색 결과 지도 보기: 검색 결과 전체를 실제 보호센터 위치에 (screens/common/SearchPlaceMap) */
export function ResultMap({ onOpen }: { onOpen: (a: Animal) => void }) {
  return <SearchPlaceMap onOpen={onOpen} />
}
