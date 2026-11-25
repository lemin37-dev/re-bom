import { Icon } from '@/components/icons/Icon'
import { benefitsFor, dotDate, programKindLabel, support } from '@/lib/adoption-support'
import type { SupportProgram } from '@/types/adoption-support'

/** 혜택 한 건: 얼마를 · 누가(조건) · 언제까지가 먼저 보이고, 누르면 신청 방법·서류·문의가 펼쳐짐 */
function BenefitCard({ p, open }: { p: SupportProgram; open: boolean }) {
  const b = p.benefit
  const insurance = p.kinds.includes('insurance') && !p.kinds.includes('cost')
  return <details className={`support-card support-fold${insurance ? ' insurance-card' : ''}`} open={open}>
    <summary>
      <span className="support-type"><Icon name={insurance ? 'check' : 'leaf'} size={17} />{programKindLabel(p)}<em>{p.sigungu ? `${p.sido} ${p.sigungu}` : `${p.sido} 전체`}</em></span>
      <span className="fold-head"><h3>{b.headline}</h3><span className="fold-toggle" aria-hidden="true"><Icon name="plus" size={16} /></span></span>
      <p className="benefit-sub">{b.percent ? `쓴 비용의 ${b.percent}%까지 · ` : ''}{b.amountMan === null ? '금액은 시·군마다 달라요 · ' : ''}{b.applyWithin}</p>
      {b.conditions.length > 0 && <span className="benefit-conditions" aria-label="받을 수 있는 조건">{b.conditions.map(c => <span key={c}><Icon name="check" size={12} />{c}</span>)}</span>}
    </summary>
    <div className="fold-body">
      <dl className="support-facts">
        {b.items.length > 0 && <div><dt>지원되는 비용</dt><dd>{b.items.join(' · ')}</dd></div>}
        <div><dt>신청 방법</dt><dd>{p.method || b.channels.join(' · ') || '담당 부서에 문의'}</dd></div>
        {p.documents && <div><dt>준비할 서류</dt><dd>{p.documents}</dd></div>}
        {p.contact && <div><dt>문의</dt><dd>{p.contact.replace(/\|\|/g, ' · ')}</dd></div>}
        {p.target && <div><dt>자세한 대상</dt><dd>{p.target}</dd></div>}
      </dl>
      <small>사업 이름: {p.name}{p.ordinance ? ` · 근거: ${p.ordinance.split('||')[0]}` : ''} · 정보 갱신일 {dotDate(p.updatedAt)}{b.budgetLimited ? ' · 예산이 끝나면 일찍 마감될 수 있어요' : ''}</small>
    </div>
  </details>
}

function NoBenefit({ place }: { place: string }) {
  return <div className="support-empty"><Icon name="pin" size={22} /><div>
    <strong>{place}에는 아직 확인된 입양 혜택이 없어요.</strong>
    <p>혜택이 없다는 뜻은 아니에요. 운영팀이 지역 혜택을 계속 확인하고 있어요. 입양할 보호센터나 시·군·구 동물보호 담당 부서에 입양비·펫보험 지원이 있는지 물어보세요.</p>
  </div></div>
}

/**
 * 한 지역에서 입양하면 받을 수 있는 혜택 (확인된 것만): 그 시·군·구 + 속한 시·도.
 * 입양 안내(지역 지원 페이지)와 공고 상세(보호소 관할 지역)에서 함께 씁니다.
 */
export function RegionSupportPanel({ sido, sigungu, compact = false }: { sido: string; sigungu: string | null; compact?: boolean }) {
  const r = benefitsFor(sido, sigungu)
  const place = sigungu ? `${r.sido} ${sigungu}` : r.sido
  const all = [...r.local, ...r.sidoWide]
  if (all.length === 0) return <NoBenefit place={place} />
  return <div className={`region-support${compact ? ' compact' : ''}`}>
    <p className="benefit-count"><strong>{place}</strong>에서 입양하면 받을 수 있는 혜택 <b>{all.length}</b>가지{r.local.length > 0 && r.sidoWide.length > 0 ? ` (${sigungu} ${r.local.length} · ${r.sido} 전체 ${r.sidoWide.length})` : ''}</p>
    <div className="program-list">{all.map(p => <BenefitCard key={p.id} p={p} open={all.length === 1} />)}</div>
    <p className="reference-note">지원은 보통 아이를 입양한 보호센터가 있는 지역에서 신청해요. 조건은 바뀔 수 있으니 신청 전에 문의처에 확인해 주세요. 출처: {support.sources.programs} · 자료 기준일 {dotDate(support.generatedAt)}</p>
  </div>
}
