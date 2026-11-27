'use client'

import { useState } from 'react'
import { Icon } from '@/components/icons/Icon'
import { SectionTitle } from '@/components/ui/SectionTitle'
import { adoptionFaqs, adoptionSteps } from '@/data/adoption-guide'
import { useExperience } from '@/providers/ExperienceProvider'
import { Checklist } from '../common/Checklist'
import { EventsExplorer } from './EventsExplorer'
import { GuideTabs } from './GuideTabs'
import { SupportExplorer } from './SupportExplorer'

function AdoptionStepper() {
  const [step, setStep] = useState(0)
  const { navigate } = useExperience()
  const current = adoptionSteps[step]
  const act = (target: string) => (target.startsWith('/') ? navigate(target) : document.getElementById(target)?.scrollIntoView({ behavior: 'smooth', block: 'start' }))
  return <>
    <div className="adoption-stepper" role="tablist" aria-label="입양 절차">{adoptionSteps.map((s, i) => <button role="tab" id={`step-tab-${i}`} aria-selected={step === i} aria-controls="step-description" className={step === i ? 'active-step' : ''} key={s.title} onClick={() => setStep(i)}><span>0{i + 1}</span><strong>{s.title}</strong></button>)}</div>
    <div className="step-description" role="tabpanel" id="step-description" aria-labelledby={`step-tab-${step}`}><span className="step-watermark">0{step + 1}</span><div>
      <span className="eyebrow">한 걸음 {step + 1} / {adoptionSteps.length}</span><h3>{current.text}</h3><div className="step-detail">{current.detail.split(/(?<=[.요])\s+/).map(line => <p key={line}>{line}</p>)}</div>
      <div className="step-tip"><Icon name="check" size={16} />{current.tip}</div>
      {current.note && <p className="step-note"><Icon name="leaf" size={15} />{current.note}</p>}
      {current.list && <div className="step-promise"><strong>{current.list.title}</strong><ul>{current.list.items.map(item => <li key={item}>{item}</li>)}</ul></div>}
      {current.action && <button type="button" className="step-action" onClick={() => act(current.action!.target)}>{current.action.label}<Icon name="arrow" size={15} /></button>}
    </div></div>
  </>
}

function FaqList() {
  const [open, setOpen] = useState<number | null>(0)
  return <div className="faq-list">{adoptionFaqs.map(([question, answer], i) => <div className="faq-item" key={question}>
    <h3><button aria-expanded={open === i} aria-controls={`faq-answer-${i}`} onClick={() => setOpen(open === i ? null : i)}><span><b>질문</b>{question}</span><span className={open === i ? 'faq-minus' : ''}><Icon name="plus" size={17} /></span></button></h3>
    <div id={`faq-answer-${i}`} hidden={open !== i}><p>{answer}</p></div>
  </div>)}</div>
}

function GuideHeading({ eyebrow, title, text }: { eyebrow: string; title: string; text: string }) {
  return <>
    <div className="page-heading"><div><div className="eyebrow"><Icon name="leaf" size={16} />{eyebrow}</div><h1>{title}</h1><p>{text}</p></div><span className="page-heading-mark"><Icon name="leaf" size={40} /></span></div>
    <GuideTabs />
  </>
}

/** /guide — 01 입양 절차 안내 (웹·앱 공통 본문) */
export function AdoptionGuide() {
  const [checked, setChecked] = useState<string[]>([])
  return <section className="guide-page">
    <GuideHeading eyebrow="함께 살아갈 내일을 준비해요" title="가족이 되는 길, 한 걸음씩" text="처음이라도 괜찮아요. 만남부터 입양 이후까지 함께 알아봐요." />
    <section className="guide-section" id="guide-steps">
      <SectionTitle number="01" title="입양 절차 안내" sub="각 단계를 눌러 준비할 일을 알아보세요." />
      <AdoptionStepper />
      <div className="guide-checklist" id="visit-checklist"><h3 className="tier-title"><span className="tier-dot sage" />방문 전 체크리스트</h3><Checklist checked={checked} onChange={setChecked} /></div>
      <p className="reference-note">입양 절차와 약속할 것은 국가동물보호정보시스템의 ‘입양 안내’를 참고해 정리했어요. 자세한 내용은 국가동물보호정보시스템 누리집의 입양 안내에서 볼 수 있어요.</p>
    </section>
    <section className="guide-section"><SectionTitle title="자주 묻는 질문" /><FaqList /></section>
  </section>
}

/** /guide/support — 02 우리 지역 입양 지원 */
export function AdoptionSupportScreen() {
  return <section className="guide-page">
    <GuideHeading eyebrow="입양 후에도 함께해요" title="우리 지역 입양 혜택" text="입양하면 받을 수 있는 입양비·펫보험 혜택을 지역별로 모았어요. 지원은 보통 아이를 입양한 보호센터의 지역 기준이에요." />
    <section className="guide-section"><SupportExplorer /></section>
  </section>
}

/** /guide/events — 03 전국 입양 행사 */
export function AdoptionEventsScreen() {
  return <section className="guide-page">
    <GuideHeading eyebrow="직접 만나며 알아가요" title="전국 입양 행사" text="전국에서 어떤 행사가 얼마나 열리는지 지도와 시·도별 개수로 보여 드려요." />
    <section className="guide-section"><EventsExplorer /></section>
  </section>
}
