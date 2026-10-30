import type { ReactNode } from 'react'
import { Icon } from '@/components/icons/Icon'
import { SectionTitle } from '@/components/ui/SectionTitle'

/** 상세 정보 섹션. 웹은 펼친 카드, 앱(compact)은 접을 수 있는 아코디언으로 표시합니다. */
export function DetailInformation({ compact, id, title, number, sub, children }: { compact: boolean; id: string; title: string; number: string; sub?: string; children: ReactNode }) {
  if (!compact) return <section className="information-section" id={id}><SectionTitle title={title} number={number} sub={sub} />{children}</section>
  return <details className="information-section mobile-information" id={id} open={id === 'pet-section-0' || undefined}>
    <summary><span><small>{number}</small>{title}</span><Icon name="plus" size={19} /></summary>
    <div className="mobile-information-content">{sub && <p className="reference-note">{sub}</p>}{children}</div>
  </details>
}
