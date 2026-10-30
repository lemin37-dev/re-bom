'use client'

import { Icon } from '@/components/icons/Icon'
import { HeroMascot } from '@/components/mascot/HeroMascot'

export function MobileHero() {
  return <section className="mobile-hero">
    <div className="eyebrow"><Icon name="leaf" size={14} />다시 만나는, 새로운 시작</div>
    <h1>나의 일상에 스며들<br /><span>그 친구를, 다시 봄.</span></h1>
    <p>문장도, 사진도, 생활 조건도.<br />함께 살아갈 이유부터 찾아드려요.</p>
    <div className="mobile-hero-art"><HeroMascot /><span className="hero-art-sprout"><Icon name="leaf" size={17} /></span></div>
  </section>
}
