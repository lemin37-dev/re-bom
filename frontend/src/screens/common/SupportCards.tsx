import { RegionSupportPanel } from './RegionSupportPanel'

/** 공고 상세의 '이 지역의 입양 지원': 보호소 관할 지역 기준 (사는 곳이 아님) */
export function SupportCards({ region, district }: { region: string; district: string }) {
  if (!region) return <p className="reference-note">보호소 주소를 몰라 지역 지원을 찾지 못했어요. 입양 절차 안내에서 지역을 검색해 보세요.</p>
  return <RegionSupportPanel sido={region} sigungu={district || null} compact />
}
