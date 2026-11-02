import type { AnimalSearchParams } from '@/lib/api'
import type { Animal } from '@/types/animal'
import type { Filters, SortOption } from '@/types/filters'

/** 화면의 지역 묶음 → 시·도 이름에 들어가는 단어 */
const REGION_WORDS: Record<string, string[]> = {
  '서울·경기': ['서울', '경기'],
  인천: ['인천'],
  강원: ['강원'],
  충청: ['충청', '충북', '충남', '대전', '세종'],
  전라: ['전라', '전북', '전남', '광주'],
  경상: ['경상', '경북', '경남', '부산', '대구', '울산'],
  제주: ['제주'],
}

/** 생활 조건 → 검색 API 조건 */
export function filtersToParams(filters: Filters): AnimalSearchParams {
  const p: AnimalSearchParams = {}
  if (filters.region !== '전국') p.region = REGION_WORDS[filters.region]
  if (filters.type === '개') p.species = 'dog'
  else if (filters.type === '고양이') p.species = 'cat'
  else if (filters.type === '기타') p.species = 'etc'
  if (filters.gender === '수컷') p.sex = 'M'
  else if (filters.gender === '암컷') p.sex = 'F'
  if (filters.age === '1살 미만') p.ageMax = 0
  else if (filters.age === '1~7살') { p.ageMin = 1; p.ageMax = 7 }
  else if (filters.age === '8살 이상') p.ageMin = 8
  if (filters.size.startsWith('소형')) p.weightLt = 8
  else if (filters.size.startsWith('중형')) { p.weightMin = 8; p.weightLt = 15 }
  else if (filters.size.startsWith('대형')) p.weightMin = 15
  return p
}

export function sortToParam(sort: SortOption): AnimalSearchParams['sort'] {
  return sort === '공고 종료 임박 순' ? 'notice_end' : 'latest'
}

/** '입양 가능 먼저'를 켰을 때 이미 불러온 목록 안에서 입양 가능한 아이를 앞으로 (순서는 유지) */
export function availableFirstOrder(list: Animal[], isAvailable: (a: Animal) => boolean) {
  return [...list].sort((a, b) => Number(isAvailable(b)) - Number(isAvailable(a)))
}
