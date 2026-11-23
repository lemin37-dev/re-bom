'use client'

import { useEffect, useState } from 'react'
import { fetchTagCatalog } from '@/lib/api'
import type { TagCatalog } from '@/types/tags'

// 웹/앱 모드를 오가며 화면이 다시 그려져도 한 번만 받도록 모듈에 둡니다. 실패하면 다음에 다시 시도합니다.
let cached: Promise<TagCatalog> | null = null

/** 사진 태그 사전. 불러오기 전이거나 실패하면 null (태그 영역을 숨김) */
export function useTagCatalog() {
  const [catalog, setCatalog] = useState<TagCatalog | null>(null)
  useEffect(() => {
    let alive = true
    cached ??= fetchTagCatalog().catch(e => { cached = null; throw e })
    cached.then(c => { if (alive) setCatalog(c) }).catch(() => {})
    return () => { alive = false }
  }, [])
  return catalog
}
