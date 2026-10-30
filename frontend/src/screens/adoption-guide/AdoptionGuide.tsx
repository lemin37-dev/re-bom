'use client'

import { useState } from 'react'
import { Icon } from '@/components/icons/Icon'
import { SectionTitle } from '@/components/ui/SectionTitle'
import { adoptionEvents, adoptionFaqs, adoptionSteps } from '@/data/adoption-guide'
import { Checklist } from '../common/Checklist'
import { RegionSelect } from '../common/RegionSelect'
import { SupportCards } from '../common/SupportCards'

function AdoptionStepper() {
  const [step, setStep] = useState(0)
  const current = adoptionSteps[step]
  return <>
    <div className="adoption-stepper" role="tablist" aria-label="입양 절차">{adoptionSteps.map((s, i) => <button role="tab" id={`step-tab-${i}`} aria-selected={step === i} aria-controls="step-description" className={step === i ? 'active-step' : ''} key={s.title} onClick={() => setStep(i)}><span>0{i + 1}</span><strong>{s.title}</strong></button>)}</div>
    <div className="step-description" role="tabpanel" id="step-description" aria-labelledby={`step-tab-${step}`}><span className="step-watermark">0{step + 1}</span><div><span className="eyebrow">한 걸음 {step + 1} / {adoptionSteps.length}</span><h3>{current.text}</h3><p>{current.detail}</p><div className="step-tip"><Icon name="check" size={16} />{current.tip}</div></div></div>
  </>
}

function FaqList() {
  const [open, setOpen] = useState<number | null>(0)
  return <div className="faq-list">{adoptionFaqs.map(([question, answer], i) => <div className="faq-item" key={question}>
    <h3><button aria-expanded={open === i} aria-controls={`faq-answer-${i}`} onClick={() => setOpen(open === i ? null : i)}><span><b>질문</b>{question}</span><span className={open === i ? 'faq-minus' : ''}><Icon name="plus" size={17} /></span></button></h3>
    <div id={`faq-answer-${i}`} hidden={open !== i}><p>{answer}</p></div>
  </div>)}</div>
}

/** 입양 절차 안내 화면 (웹·앱 공통 본문) */
export function AdoptionGuide() {
  const [region, setRegion] = useState('경기도')
  const [district, setDistrict] = useState('수원시')
  const [checked, setChecked] = useState<string[]>([])

  return <section className="guide-page">
    <div className="page-heading"><div><div className="eyebrow"><Icon name="leaf" size={16} />함께 살아갈 내일을 준비해요</div><h1>가족이 되는 길, 한 걸음씩</h1><p>처음이라도 괜찮아요. 만남부터 입양 이후까지 함께 알아봐요.</p></div><span className="page-heading-mark"><Icon name="leaf" size={40} /></span></div>
    <div className="local-selector"><div><Icon name="pin" size={22} /><span><strong>내 지역을 알려주세요</strong><small>지역에 맞는 지원 예시와 가까운 행사를 보여드려요.</small></span></div><RegionSelect region={region} district={district} onChange={(r, d) => { setRegion(r); setDistrict(d) }} /></div>
    <section className="guide-section"><SectionTitle title="입양까지의 여섯 걸음" sub="각 단계를 눌러 준비할 일을 알아보세요." /><AdoptionStepper /></section>
    <section className="guide-section"><SectionTitle title="우리 지역 지원" sub={`${region} ${district} · 선택한 지역 기준 예시`} /><SupportCards region={region} district={district} /></section>
    <section className="guide-section"><SectionTitle title="가까운 입양 행사" sub="직접 만나며 서로를 알아가는 시간" />
      <div className="event-grid">{adoptionEvents.map(([date, day, title, place]) => <article className="event-card" key={date}><div className="event-date"><strong>{date}</strong><span>2026 · {day}요일</span></div><div><span className="event-badge">예시 행사</span><h3>{title}</h3><p><Icon name="pin" size={13} />{region} {district} {place}</p></div></article>)}</div>
      <p className="reference-note">행사 정보는 운영팀이 직접 모은 정보예요. 이 화면의 행사명·일정·장소는 모두 가상 예시입니다.</p>
    </section>
    <div className="guide-bottom-grid">
      <section className="guide-section"><SectionTitle title="자주 묻는 질문" /><FaqList /></section>
      <section className="guide-section" id="visit-checklist"><SectionTitle title="방문 전 체크리스트" /><Checklist checked={checked} onChange={setChecked} /></section>
    </div>
  </section>
}
