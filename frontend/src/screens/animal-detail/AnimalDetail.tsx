'use client'

import { useState, type MouseEvent } from 'react'
import { createPortal } from 'react-dom'
import { PetCard } from '@/components/animal/PetCard'
import { Icon } from '@/components/icons/Icon'
import { SectionTitle } from '@/components/ui/SectionTitle'
import { shelterOf } from '@/data/shelters'
import { behaviorTags, noticeNumber as toNoticeNumber, similarAnimals } from '@/lib/animal'
import type { Animal } from '@/types/animal'
import { SupportCards } from '../common/SupportCards'
import { DetailInformation } from './DetailInformation'
import { DetailModal, type DetailModalKind } from './DetailModal'
import { PetGallery } from './PetGallery'
import { ProfilePanel } from './ProfilePanel'

const sectionLabels = ['공고 정보', '외형 정보', '성격·생활', '건강', '보호소', '지역 지원', '비슷한 아이들']

type Props = {
  animal: Animal
  onBack: () => void
  onGuide: () => void
  onOpen: (a: Animal) => void
  /** 앱 모드: 정보 섹션을 접고, 문의 버튼을 하단 고정 영역(actionTarget)에 표시 */
  compact?: boolean
  actionTarget?: HTMLElement | null
}

export function AnimalDetail({ animal: a, onBack, onGuide, onOpen, compact = false, actionTarget }: Props) {
  const [gallery, setGallery] = useState(0)
  const [modal, setModal] = useState<DetailModalKind | null>(null)
  const [checked, setChecked] = useState<string[]>([])
  const center = shelterOf(a)
  const noticeNumber = toNoticeNumber(a, center)
  const behavior = behaviorTags(a)

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
      <ProfilePanel animal={a} noticeNumber={noticeNumber} compact={compact} onContact={() => setModal('contact')} onChecklist={() => setModal('checklist')} />
    </div>
    <div className="detail-section-nav">{sectionLabels.map((label, i) => <a href={`#pet-section-${i}`} key={label} onClick={e => jumpToSection(e, i)}>{label}</a>)}</div>

    <DetailInformation compact={compact} id="pet-section-0" number="01" title="공고 정보" sub="보호센터가 전하는 아이의 이야기예요.">
      <dl className="notice-facts"><div><dt>공고상 품종</dt><dd>{a.kind}</dd></div><div><dt>발견 장소</dt><dd>{center.region} {center.district} 봄빛공원 인근 (가상 장소)</dd></div><div><dt>보호 상태</dt><dd>{a.available ? '보호 중 · 입양 상담 가능' : '보호 중 · 소유자 확인 공고 진행'}</dd></div><div><dt>공고 기간</dt><dd>2026.{a.available ? '10.12 ~ 10.22' : '10.22 ~ 11.01'}</dd></div></dl>
      <div className="original-quote"><span>공고 특징 · 원문</span><p>“{a.quote}”</p></div>
    </DetailInformation>

    <DetailInformation compact={compact} id="pet-section-1" number="02" title="외형 정보" sub="생김새는 참고만, 가족이 될 이유는 일상에서 찾아요.">
      <div className="breed-fixed">공고상 품종: <strong>{a.kind}</strong></div>
      <div className="appearance-tags">{[a.kg < 8 ? '소형' : '중형', '짧은 털', `${a.color} 계열`, a.id === 1 || a.id === 3 || a.cat ? '선 귀' : '처진 귀', a.cat ? '긴 꼬리' : '말린 꼬리'].map(tag => <span key={tag}>{tag}</span>)}</div>
      <div className="resemblance"><h3>외형이 닮은 품종 <span>(참고용, 혈통 아님)</span></h3><ol>{(a.cat ? ['코리안숏헤어', '아메리칸숏헤어', '유러피안숏헤어'] : ['진돗개', '시바견', '웰시코기']).map((breed, i) => <li key={breed}><span>{i + 1}</span>{breed}</li>)}</ol></div>
      <p className="info-note"><Icon name="leaf" size={16} />외형 정보는 사진과 공고를 바탕으로 자동 정리한 참고 정보예요. 성격은 외형으로 판단하지 않아요.</p>
      <p className="reference-note">이 화면의 외형 태그는 기능 설명을 위한 가상 예시예요.</p>
    </DetailInformation>

    <DetailInformation compact={compact} id="pet-section-2" number="03" title="성격·생활" sub="외형이 아닌, 공고에 적힌 이야기를 바탕으로 정리했어요.">
      <div className="behavior-tags">{(behavior.length ? behavior : ['자세한 생활 습관은 보호소 확인 필요']).map(tag => <span key={tag}>{tag}<small>공고 문장 기반</small></span>)}</div>
      <p className="reference-note">새로운 환경에서는 다른 모습을 보일 수 있어요. 함께 지내는 모습은 보호소와 충분히 이야기해 주세요.</p>
    </DetailInformation>

    <DetailInformation compact={compact} id="pet-section-3" number="04" title="건강">
      <div className="health-grid">{[['중성화', a.care.includes('중성화') ? a.care : '보호소 확인 필요'], ['예방접종', '보호소 확인 필요'], ['건강검진', a.care.includes('건강검진') ? a.care : '보호소 확인 필요']].map(([label, value]) => <div key={label}><span className="soft-icon"><Icon name={value.includes('완료') ? 'check' : 'plus'} size={20} /></span><span>{label}<strong className={value === '보호소 확인 필요' ? 'unconfirmed' : ''}>{value}</strong></span></div>)}</div>
    </DetailInformation>

    <DetailInformation compact={compact} id="pet-section-4" number="05" title="보호소">
      <div className="shelter-detail-layout">
        <div className="shelter-contact"><span className="operation-badge">{center.type} 운영</span><h3>{center.name}</h3><p><Icon name="pin" size={16} />{center.address}</p><p>전화 <strong>{center.phone} (예시 번호 · 연결 불가)</strong></p><p>운영 시간 <strong>{center.hours}</strong></p><p>내 위치에서 <strong>{center.distance}km (예시 거리)</strong></p><button className="outline-button" onClick={() => setModal('contact')}>문의 전 안내 확인하기<Icon name="arrow" size={15} /></button></div>
        <div className="shelter-map-placeholder"><Icon name="pin" size={30} /><strong>{center.name}</strong><span>지도 자리표시 · 실제 위치가 아닙니다</span></div>
      </div>
    </DetailInformation>

    <section className="information-section" id="pet-section-5">
      <div className="section-with-link"><SectionTitle number="06" title="이 지역의 입양 지원" sub={`${center.region} ${center.district} 기준 예시 안내`} /><button className="text-link" onClick={onGuide}>입양 절차 안내 보기<Icon name="arrow" size={16} /></button></div>
      <SupportCards region={center.region} district={center.district} />
    </section>

    <section className="similar-section" id="pet-section-6"><SectionTitle number="07" title="비슷한 아이들" sub="또 다른 인연도 천천히 만나 보세요." /><div className="similar-grid">{similarAnimals(a).map(p => <PetCard key={p.id} animal={p} onOpen={onOpen} />)}</div></section>

    {compact && actionTarget && createPortal(<div className="mobile-detail-actions"><button className="outline-button" onClick={() => setModal('checklist')}><Icon name="check" size={18} /><span>준비물</span></button><button className="peach-button" onClick={() => setModal('contact')}>보호소에 문의하기<Icon name="arrow" size={18} /></button></div>, actionTarget)}
    <DetailModal kind={modal} center={center} noticeNumber={noticeNumber} checked={checked} onChecked={setChecked} onClose={() => setModal(null)} />
  </section>
}
