'use client'

import { Icon } from '@/components/icons/Icon'
import { ROUTES } from '@/constants/routes'
import { useExperience } from '@/providers/ExperienceProvider'
import { useSearch } from '@/providers/SearchProvider'
import { MobileHero } from '../components/MobileHero'
import { MobileResults } from '../components/MobileResults'
import { MobileSearchForm } from '../components/MobileSearchForm'
import { MobileSuggestions } from '../components/MobileSuggestions'

/** 앱 입양 찾기 화면 배치: 히어로 → 검색 박스 → 센터 조건 → 추천 → 결과 → 입양 안내 배너 */
export function MobileSearchScreen() {
  const { searched, centerFilter, clearCenter } = useSearch()
  const { navigate } = useExperience()

  return <div className="mobile-discovery">
    <MobileHero />
    <MobileSearchForm />
    {centerFilter && <div className="mobile-center-filter"><Icon name="pin" size={17} /><span>{centerFilter}</span><button onClick={clearCenter} aria-label="보호센터 조건 해제"><Icon name="close" size={16} /></button></div>}
    {!searched && <MobileSuggestions />}
    <MobileResults />
    <button className="mobile-guide-banner" onClick={() => navigate(ROUTES.guide)}><span className="soft-icon"><Icon name="leaf" size={23} /></span><span><strong>처음 입양을 고민하고 있나요?</strong><small>서두르지 않아도 괜찮아요. 함께 알아봐요.</small></span><Icon name="arrow" size={18} /></button>
  </div>
}
