import { animals } from '@/data/animals'
import type { Animal, Shelter } from '@/types/animal'

export function statusLabel(a: Animal) {
  return a.available ? '입양 가능' : '공고 중 D-3'
}

export function availabilityLabel(a: Animal) {
  return a.available ? '지금 입양 상담 가능' : '11월 1일부터 입양 가능'
}

export function noticeNumber(a: Animal, center: Shelter) {
  const regionCode = center.region === '경기도' ? '경기' : center.region === '서울특별시' ? '서울' : '인천'
  return `${regionCode}-${center.name.split(' ')[0].replace('동물보호센터', '')}-2026-${String(122 + a.id).padStart(5, '0')}`
}

/** 공고 문장에서 성격·생활 태그 추출 */
export function behaviorTags(a: Animal) {
  return [
    ...(a.quote.includes('사람') && a.quote.includes('따르') ? ['사람을 잘 따름'] : []),
    ...(a.quote.includes('산책') ? ['산책 좋아함'] : []),
    ...(a.quote.includes('짖음') ? ['짖음 적음'] : []),
    ...(a.quote.includes('배변') ? ['실내 배변 가능'] : []),
    ...(a.quote.includes('잘 지냄') ? ['다른 동물과 잘 지냄'] : []),
    ...(a.quote.includes('품을') ? ['사람 품을 좋아함'] : []),
  ]
}

/** 같은 종(개/고양이)을 먼저 보여주는 비슷한 아이들 */
export function similarAnimals(a: Animal, count = 3) {
  return animals
    .filter(p => p.id !== a.id)
    .sort((x, y) => Number(Boolean(y.cat) === Boolean(a.cat)) - Number(Boolean(x.cat) === Boolean(a.cat)))
    .slice(0, count)
}
