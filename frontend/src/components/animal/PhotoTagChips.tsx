'use client'

import { useSearch } from '@/providers/SearchProvider'
import type { PhotoTag } from '@/types/tags'

/** 공고 카드의 사진 태그 칩. 지금 고른 태그와 맞는 칩은 강조해서 왜 결과에 나왔는지 보여 줍니다. */
export function PhotoTagChips({ tags }: { tags: PhotoTag[] }) {
  const { tags: selected } = useSearch()
  if (!tags.length) return null
  return <span className="photo-tags">
    {tags.map(t => {
      const matched = selected.includes(t.code)
      return <span key={t.code} className={matched ? 'matched' : ''}>{t.label}{matched && <span className="sr-only"> (고른 태그)</span>}</span>
    })}
  </span>
}
