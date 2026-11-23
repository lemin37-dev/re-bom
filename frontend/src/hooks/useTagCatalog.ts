'use client'

import { useEffect, useState } from 'react'
import { fetchTagCatalog } from '@/lib/api'
import { TAG_SPECIES, type TagCatalog, type TagSpecies } from '@/types/tags'

type Catalogs = Record<TagSpecies, TagCatalog>

// 강아지·고양이 태그 세트를 한 번에 받아 둡니다. 탭을 바꿔도 다시 받지 않고, 웹/앱 모드를 오가도 한 번만 받습니다.
let cached: Promise<Catalogs> | null = null

function load(): Promise<Catalogs> {
  return Promise.all(TAG_SPECIES.map(s => fetchTagCatalog(s.key)))
    .then(list => Object.fromEntries(list.map(c => [c.species, c])) as Catalogs)
}

/** 종별 사진 태그 사전. 불러오기 전이거나 실패하면 null (태그 영역을 숨김) */
export function useTagCatalogs() {
  const [catalogs, setCatalogs] = useState<Catalogs | null>(null)
  useEffect(() => {
    let alive = true
    cached ??= load().catch(e => { cached = null; throw e })
    cached.then(c => { if (alive) setCatalogs(c) }).catch(() => {})
    return () => { alive = false }
  }, [])
  return catalogs
}
