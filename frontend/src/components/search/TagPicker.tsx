'use client'

import { useId, useState } from 'react'
import { Icon } from '@/components/icons/Icon'
import { useTagCatalog } from '@/hooks/useTagCatalog'
import { useSearch } from '@/providers/SearchProvider'
import type { TagOption } from '@/types/tags'

/**
 * 사진 태그로 찾기: 추천 태그 → 큰 범주(털 · 귀 모양 · 눈매)를 눌러 상세 태그 고르기 → 고른 태그.
 * 태그를 누르면 바로 다시 검색합니다. 웹은 검색 카드 아래, 앱은 검색 박스 안에 놓이며 compact 로 크기만 맞춥니다.
 */
export function TagPicker({ compact = false }: { compact?: boolean }) {
  const catalog = useTagCatalog()
  const { tags, toggleTag, clearTags } = useSearch()
  const [open, setOpen] = useState<string | null>(null)
  const panelId = useId()

  if (!catalog || !catalog.categories.some(c => c.tags.some(t => t.count > 0))) return null

  const byCode = new Map<string, TagOption>(catalog.categories.flatMap(c => c.tags.map(t => [t.code, t] as const)))
  const openCategory = catalog.categories.find(c => c.key === open)
  const chip = (t: TagOption) => {
    const on = tags.includes(t.code)
    return <button key={t.code} type="button" className={`tag-chip ${on ? 'on' : ''}`} aria-pressed={on}
      disabled={!on && t.count === 0} onClick={() => toggleTag(t.code)}>
      {on && <Icon name="check" size={12} />}{t.label}
    </button>
  }

  return <section className={`tag-picker ${compact ? 'compact' : ''}`} aria-label="사진 태그로 찾기">
    <div className="tag-picker-head">
      <strong>태그로 찾기</strong>
      <span>대표 사진을 보고 AI가 붙인 태그예요 · 강아지만</span>
    </div>

    {catalog.recommended.length > 0 && <div className="tag-row tag-recommended">
      <span className="tag-row-label">추천</span>
      <div className="tag-chips">{catalog.recommended.map(code => byCode.get(code)).filter(t => t !== undefined).map(chip)}</div>
    </div>}

    <div className="tag-row">
      <span className="tag-row-label">범주</span>
      <div className="tag-chips">
        {catalog.categories.map(c => {
          const picked = c.tags.filter(t => tags.includes(t.code)).length
          const isOpen = open === c.key
          return <button key={c.key} type="button" className={`tag-category ${isOpen ? 'open' : ''} ${picked ? 'picked' : ''}`}
            aria-expanded={isOpen} aria-controls={isOpen ? panelId : undefined} onClick={() => setOpen(isOpen ? null : c.key)}>
            {c.label}{picked > 0 && <b>{picked}</b>}<Icon name="plus" size={12} />
          </button>
        })}
      </div>
    </div>

    {openCategory && <div className="tag-panel" id={panelId} role="group" aria-label={`${openCategory.label} 상세 태그`}>
      <div className="tag-chips">{openCategory.tags.map(chip)}</div>
      <p>고른 태그가 맞는 아이부터 보여 드려요. 같은 범주에서 여러 개를 고르면 그중 하나만 맞아도 돼요. 털은 고른 것과 반대인 아이만 빼요.</p>
    </div>}

    {tags.length > 0 && <div className="tag-row tag-selected">
      <span className="tag-row-label">고른 태그</span>
      <div className="tag-chips">
        {tags.map(code => <button key={code} type="button" className="tag-chip on removable"
          onClick={() => toggleTag(code)} aria-label={`${byCode.get(code)?.label ?? code} 태그 빼기`}>
          {byCode.get(code)?.label ?? code}<Icon name="close" size={12} />
        </button>)}
        <button type="button" className="tag-clear" onClick={clearTags}>모두 지우기</button>
      </div>
    </div>}
  </section>
}
