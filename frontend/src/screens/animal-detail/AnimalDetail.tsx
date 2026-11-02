'use client'

import { useState, type MouseEvent } from 'react'
import { createPortal } from 'react-dom'
import { PetCard } from '@/components/animal/PetCard'
import { Icon } from '@/components/icons/Icon'
import { SectionTitle } from '@/components/ui/SectionTitle'
import { behaviorTags, isAvailable, noticePeriod, sizeLabel } from '@/lib/animal'
import type { Animal } from '@/types/animal'
import { SupportCards } from '../common/SupportCards'
import { DetailInformation } from './DetailInformation'
import { DetailModal, type DetailModalKind } from './DetailModal'
import { PetGallery } from './PetGallery'
import { ProfilePanel } from './ProfilePanel'

const sectionLabels = ['공고 정보', '외형 정보', '성격·생활', '건강', '보호소', '지역 지원', '비슷한 아이들']
const UNCONFIRMED = '보호소 확인 필요'

type Props = {
  animal: Animal
  similar: Animal[]
  onBack: () => void
  onGuide: () => void
  onOpen: (a: Animal) => void
  /** 앱 모드: 정보 섹션을 접고, 문의 버튼을 하단 고정 영역(actionTarget)에 표시 */
  compact?: boolean
  actionTarget?: HTMLElement | null
}

function protectionLabel(a: Animal) {
  if (!a.protectedNow) return a.endReason ? `${a.state} · ${a.endReason}` : a.state
  return isAvailable(a) ? '보호 중 · 입양 상담 가능' : '보호 중 · 소유자 확인 공고 진행'
}

export function AnimalDetail({ animal: a, similar, onBack, onGuide, onOpen, compact = false, actionTarget }: Props) {
  const [gallery, setGallery] = useState(0)
  const [modal, setModal] = useState<DetailModalKind | null>(null)
  const [checked, setChecked] = useState<string[]>([])
  const center = a.shelter
  const behavior = behaviorTags(a)
  const size = sizeLabel(a)
  const neutered = a.care === '중성화 완료' ? a.care : a.care === '중성화 안 함' ? '안 함' : UNCONFIRMED
  const health: [string, string][] = [
    ['중성화', neutered],
    ['예방접종', a.vaccinationCheck || UNCONFIRMED],
    ['건강검진', a.healthCheck || UNCONFIRMED],
  ]

  function jumpToSection(e: MouseEvent, index: number) {
    if (!compact) return
    e.preventDefault()
    const section = document.getElementById(`pet-section-${index}`)
    if (section instanceof HTMLDetailsElement) section.open = true
    section?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }

  return <section className="expanded-detail">
    <div className="breadcrumb" aria-label="현재 경로"><button onClick={onBack}>입양 찾기</button><span>›</span><button onClick={onBack}>검색 결과</button><span>›</span><strong>상세</strong></div>
    <div className="profile-layout">
      <PetGallery animal={a} view={gallery} onView={setGallery} />
      <ProfilePanel animal={a} compact={compact} onContact={() => setModal('contact')} onChecklist={() => setModal('checklist')} />
    </div>
    <div className="detail-section-nav">{sectionLabels.map((label, i) => <a href={`#pet-section-${i}`} key={label} onClick={e => jumpToSection(e, i)}>{label}</a>)}</div>

    <DetailInformation compact={compact} id="pet-section-0" number="01" title="공고 정보" sub="보호센터가 전하는 아이의 이야기예요.">
      <dl className="notice-facts"><div><dt>공고상 품종</dt><dd>{a.kind}</dd></div><div><dt>발견 장소</dt><dd>{a.happenPlace || '공고에 없음'}</dd></div><div><dt>보호 상태</dt><dd>{protectionLabel(a)}</dd></div><div><dt>공고 기간</dt><dd>{noticePeriod(a)}</dd></div></dl>
      <div className="original-quote"><span>공고 특징 · 원문</span><p>{a.quote ? `“${a.quote}”` : '보호소가 적은 특징 문장이 없어요.'}</p></div>
    </DetailInformation>

    <DetailInformation compact={compact} id="pet-section-1" number="02" title="외형 정보" sub="생김새는 참고만, 가족이 될 이유는 일상에서 찾아요.">
      <div className="breed-fixed">공고상 품종: <strong>{a.kind}</strong></div>
      <div className="appearance-tags">{[size, a.weightLabel, a.color].filter(Boolean).map(tag => <span key={tag}>{tag}</span>)}</div>
      <p className="info-note"><Icon name="leaf" size={16} />공고에 적힌 체중·색상이에요. 사진으로 털 길이·귀 모양 등을 정리하는 기능은 준비 중이에요.</p>
    </DetailInformation>

    <DetailInformation compact={compact} id="pet-section-2" number="03" title="성격·생활" sub="외형이 아닌, 공고에 적힌 이야기를 바탕으로 정리했어요.">
      <div className="behavior-tags">{(behavior.length ? behavior : ['자세한 생활 습관은 보호소 확인 필요']).map(tag => <span key={tag}>{tag}<small>공고 문장 기반</small></span>)}</div>
      {a.sociality && <div className="original-quote"><span>사회성 · 원문</span><p>“{a.sociality}”</p></div>}
      <p className="reference-note">새로운 환경에서는 다른 모습을 보일 수 있어요. 함께 지내는 모습은 보호소와 충분히 이야기해 주세요.</p>
    </DetailInformation>

    <DetailInformation compact={compact} id="pet-section-3" number="04" title="건강">
      <div className="health-grid">{health.map(([label, value]) => <div key={label}><span className="soft-icon"><Icon name={value !== UNCONFIRMED && value !== '안 함' ? 'check' : 'plus'} size={20} /></span><span>{label}<strong className={value === UNCONFIRMED ? 'unconfirmed' : ''}>{value}</strong></span></div>)}</div>
      {a.healthNote && <div className="original-quote"><span>건강 · 원문</span><p>“{a.healthNote}”</p></div>}
    </DetailInformation>

    <DetailInformation compact={compact} id="pet-section-4" number="05" title="보호소">
      <div className="shelter-detail-layout">
        <div className="shelter-contact"><h3>{center.name}</h3><p><Icon name="pin" size={16} />{center.addr || '주소 미기재'}</p><p>전화 <strong>{center.tel ? <a href={`tel:${center.tel}`}>{center.tel}</a> : '미기재'}</strong></p><p>운영 시간 <strong>{UNCONFIRMED}</strong></p><button className="outline-button" onClick={() => setModal('contact')}>문의 전 안내 확인하기<Icon name="arrow" size={15} /></button></div>
        <div className="shelter-map-placeholder"><Icon name="pin" size={30} /><strong>{center.name}</strong><span>지도 자리표시 · 실제 위치가 아닙니다</span></div>
      </div>
    </DetailInformation>

    <section className="information-section" id="pet-section-5">
      <div className="section-with-link"><SectionTitle number="06" title="이 지역의 입양 지원" sub={`${center.sido ?? ''} ${center.sigungu ?? ''} 기준 예시 안내`} /><button className="text-link" onClick={onGuide}>입양 절차 안내 보기<Icon name="arrow" size={16} /></button></div>
      <SupportCards region={center.sido ?? ''} district={center.sigungu ?? ''} />
    </section>

    {similar.length > 0 && <section className="similar-section" id="pet-section-6"><SectionTitle number="07" title="비슷한 아이들" sub="같은 종류의 다른 친구도 천천히 만나 보세요." /><div className="similar-grid">{similar.map(p => <PetCard key={p.id} animal={p} onOpen={onOpen} />)}</div></section>}

    {compact && actionTarget && createPortal(<div className="mobile-detail-actions"><button className="outline-button" onClick={() => setModal('checklist')}><Icon name="check" size={18} /><span>준비물</span></button><button className="peach-button" onClick={() => setModal('contact')}>보호소에 문의하기<Icon name="arrow" size={18} /></button></div>, actionTarget)}
    <DetailModal kind={modal} center={center} noticeNumber={a.noticeNo} checked={checked} onChecked={setChecked} onClose={() => setModal(null)} />
  </section>
}
