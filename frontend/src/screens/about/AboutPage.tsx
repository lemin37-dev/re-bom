'use client'

import { Icon, type IconName } from '@/components/icons/Icon'
import { SectionTitle } from '@/components/ui/SectionTitle'

const stats: [num: string, unit: string, title: string, note?: string][] = [
  ['95,685', '마리', '2025년 구조된 유실·유기동물'],
  ['25.6', '%', '그중 새 가족을 만난 비율'],
  ['43.0', '%', '보호소에서 생을 마친 비율', '자연사 + 인도적 처리'],
]
const flow: [icon: IconName, title: string, text: string][] = [
  ['search', '문장·사진·조건으로 찾기', '함께 살 공간과 시간, 원하는 성격을 이야기해 주세요. 사진도 함께 넣을 수 있어요.'],
  ['leaf', '잘 맞는 이유 확인', '어떤 점에서 잘 맞는지, 내 문장·사진·조건의 근거를 함께 살펴요.'],
  ['calendar', '보호소에서 첫 만남', '보호소와 충분히 상담하고, 아이의 속도에 맞춰 직접 만나 보세요.'],
]
const promises: [title: string, text: string][] = [
  ['매일, 전국 공고를 새롭게', '전국 구조동물 공고를 매일 갱신하는 서비스 구성이에요. 현재 프로토타입은 실시간 연동 없이 가상 데이터로 동작해요.'],
  ['원본을 기준으로 안내해요', '공고 원본은 농림축산식품부 국가동물보호정보시스템이며, 공공데이터포털을 통해 제공돼요.'],
  ['외형과 성격을 구분해요', '외형 정보는 참고용이며 혈통을 의미하지 않아요. 성격·생활 정보는 공고 문장을 기준으로 정리하고, 확인되지 않은 정보는 보호소에 물어보도록 안내해요.'],
]

/** 서비스 소개 화면 (웹·앱 공통 본문) */
export function AboutPage({ onFind }: { onFind: () => void }) {
  return <section className="service-about">
    <div className="about-hero"><span className="about-overline"><Icon name="leaf" size={18} />다시 보고, 다시 만나는 봄</span><h1>보호소의 아이들을,<br /><span>다시 봄</span></h1><p>목록을 넘기는 대신, 나와 맞는 이유부터 보여 드려요.</p><div className="about-small-line" /></div>
    <section><SectionTitle title="숫자 너머에, 한 생명이 있어요" sub="다시 볼수록, 더 많은 새로운 시작이 생길 수 있도록." />
      <div className="stat-grid">{stats.map(([num, unit, title, note], i) => <article className={`stat-card stat-${i}`} key={num}><span>0{i + 1}</span><div><strong>{num}</strong><b>{unit}</b></div><h3>{title}</h3>{note && <p>{note}</p>}</article>)}</div>
      <p className="reference-note">출처: 농림축산식품부 반려동물 보호·복지 실태조사(2025)<br />위 통계는 요청하신 자료 기준으로 표시했으며, 동물·보호센터 정보는 가상 데이터입니다.</p>
    </section>
    <section className="about-flow-section"><SectionTitle title="어떻게 찾아 주나요" sub="나의 일상에서 시작해, 보호소의 첫 만남까지." />
      <div className="about-flow">{flow.map(([icon, title, text], i) => <article key={title}><span className="flow-icon"><Icon name={icon} size={28} /></span><small>0{i + 1}</small><h3>{title}</h3><p>{text}</p>{i < flow.length - 1 && <span className="flow-arrow"><Icon name="arrow" size={23} /></span>}</article>)}</div>
    </section>
    <section className="data-promise"><div><div className="eyebrow">믿고 살펴볼 수 있도록</div><h2>데이터는 투명하게,<br />판단은 신중하게.</h2></div>
      <div className="data-promise-items">{promises.map(([title, text]) => <article key={title}><Icon name="check" size={17} /><div><h3>{title}</h3><p>{text}</p></div></article>)}</div>
    </section>
    <div className="about-cta"><Icon name="leaf" size={24} /><h2>한 번의 만남이, 평생의 봄으로.</h2><p>새로운 가족을 기다리는 친구를 천천히 만나 보세요.</p><button className="primary-button" onClick={onFind}>나와 맞는 친구 찾아보기<Icon name="arrow" size={18} /></button></div>
  </section>
}
