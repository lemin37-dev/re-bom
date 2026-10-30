'use client'

import { Icon } from '@/components/icons/Icon'
import { HeroMascot } from '@/components/mascot/HeroMascot'
import { MascotArt } from '@/components/mascot/MascotArt'
import { ROUTES } from '@/constants/routes'
import { useExperience } from '@/providers/ExperienceProvider'
import { useSearch } from '@/providers/SearchProvider'
import { CenterFilterBanner } from '../components/CenterFilterBanner'
import { SearchCard } from '../components/SearchCard'
import { SearchResults } from '../components/SearchResults'
import { SearchSuggestions } from '../components/SearchSuggestions'

/** 웹 입양 찾기 화면 배치: 히어로 → 센터 배너 → 검색 카드 → 추천 → 결과 → 안내 */
export function WebSearchScreen() {
  const { searched, centerFilter, clearCenter } = useSearch()
  const { navigate } = useExperience()

  return <>
    <section className="hero"><div className="eyebrow"><Icon name="leaf" size={15} />다시 만나는, 새로운 시작</div><h1>어떤 친구를 <span>다시 봄</span>으로 만날까요?</h1><p>문장 · 사진 · 조건을 함께 넣으면, 전국 보호소 공고에서 잘 맞는 아이를 찾아드려요.</p><HeroMascot className="web-hero-mascot" /></section>
    {centerFilter && <CenterFilterBanner center={centerFilter} onClear={clearCenter} />}
    <SearchCard />
    {!searched && <SearchSuggestions />}
    <SearchResults />
    <div className="gentle-note"><MascotArt pose="cheer" /><div><strong>입양은 함께 살아갈 날들을 약속하는 일이에요.</strong><p>충분히 알아보고, 직접 만나고, 천천히 결정해도 괜찮아요.</p></div><button onClick={() => navigate(ROUTES.guide)}>입양 전 알아보기<Icon name="arrow" size={17} /></button></div>
  </>
}
