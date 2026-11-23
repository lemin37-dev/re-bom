'use client'

import { useSearch } from '@/providers/SearchProvider'
import type { PhotoTag, TagMatch } from '@/types/tags'

/**
 * 공고 카드의 사진 태그 칩. 지금 고른 태그와 맞는 칩은 강조해서 왜 결과에 나왔는지 보여 줍니다.
 * 태그로 검색했는데 이 아이에게 태그가 없으면, 맞는 아이 뒤에 나온 이유로 '사진 태그 없음'을 보여 줍니다.
 */
export function PhotoTagChips({ tags, tagMatch }: { tags: PhotoTag[]; tagMatch: TagMatch | null }) {
  const { tags: selected } = useSearch()
  if (!tags.length) return tagMatch ? <span className="photo-tags"><span className="none">사진 태그 없음</span></span> : null
  return <span className="photo-tags">
    {tags.map(t => {
      const matched = selected.includes(t.code)
      return <span key={t.code} className={matched ? 'matched' : ''}>{t.label}{matched && <span className="sr-only"> (고른 태그)</span>}</span>
    })}
  </span>
}
