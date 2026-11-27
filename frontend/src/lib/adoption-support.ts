import snapshot from '@/data/adoption-support.json'
import type { AdoptionSupportSnapshot, OrdinanceKind, SupportOrdinance, SupportProgram } from '@/types/adoption-support'

/**
 * 우리 지역 입양 지원: 스냅샷에서 지역별로 찾기.
 * - 신청할 수 있는 지원(공공서비스 혜택 정보)과 조례에 근거가 있는 지원(자치법규 자동 분류)은 화면에서 꼭 나눠 보여 줍니다.
 * - 외부 사이트 링크는 붙이지 않습니다(팀 회의 전까지). 문의처는 글로만.
 */
export const support = snapshot as AdoptionSupportSnapshot

/** 시·도 (지도·검색에서 씀) */
export const SIDOS: { sido: string; short: string }[] = [
  { sido: '서울특별시', short: '서울' }, { sido: '부산광역시', short: '부산' }, { sido: '대구광역시', short: '대구' }, { sido: '인천광역시', short: '인천' },
  { sido: '대전광역시', short: '대전' }, { sido: '울산광역시', short: '울산' }, { sido: '세종특별자치시', short: '세종' }, { sido: '경기도', short: '경기' },
  { sido: '강원특별자치도', short: '강원' }, { sido: '충청북도', short: '충북' }, { sido: '충청남도', short: '충남' }, { sido: '전북특별자치도', short: '전북' },
  { sido: '전남광주통합특별시', short: '전남·광주' }, { sido: '경상북도', short: '경북' }, { sido: '경상남도', short: '경남' }, { sido: '제주특별자치도', short: '제주' },
]

/** 옛 이름·줄임말 → 지금 시·도 이름 */
const SIDO_ALIAS: Record<string, string> = {
  광주광역시: '전남광주통합특별시',
  전라남도: '전남광주통합특별시',
  강원도: '강원특별자치도',
  전라북도: '전북특별자치도',
  제주도: '제주특별자치도',
  세종시: '세종특별자치시',
}
for (const t of SIDOS) SIDO_ALIAS[t.short] = t.sido

export function normalizeSido(name: string | null | undefined): string | null {
  if (!name) return null
  const n = name.trim()
  return SIDO_ALIAS[n] ?? n
}

const squash = (s: string) => s.replace(/\s+/g, '')

export const KIND_LABEL: Record<OrdinanceKind, string> = {
  cost: '입양비·진료비',
  insurance: '펫보험',
  education: '입양 교육',
  center: '입양센터',
  event: '입양 행사·홍보',
  aftercare: '입양 후 사후관리',
  goods: '물품',
  fee: '동물등록 수수료 감면',
  basic: '입양 활성화 노력',
}
/** 화면에 보여 줄 순서 */
export const KIND_ORDER: OrdinanceKind[] = ['cost', 'insurance', 'fee', 'education', 'center', 'event', 'aftercare', 'goods', 'basic']

export type RegionSupport = {
  sido: string
  sigungu: string | null
  /** 그 지역(시·군·구)의 신청할 수 있는 지원 */
  programs: SupportProgram[]
  /** 그 지역 조례 */
  ordinances: SupportOrdinance[]
  /** 속한 시·도의 지원 (시·군·구를 고른 경우) */
  sidoPrograms: SupportProgram[]
  sidoOrdinances: SupportOrdinance[]
}

const sameSigungu = (a: string | null, b: string | null) => (a && b ? squash(a) === squash(b) : a === b)

/** 시·도(+시·군·구)의 입양 지원 */
export function supportFor(sidoName: string, sigungu: string | null): RegionSupport {
  const sido = normalizeSido(sidoName) ?? sidoName
  const inSido = <T extends { sido: string | null }>(x: T) => normalizeSido(x.sido) === sido
  const programs = support.programs.filter(p => inSido(p) && sameSigungu(p.sigungu, sigungu))
  const ordinances = support.ordinances.filter(o => inSido(o) && sameSigungu(o.sigungu, sigungu))
  return {
    sido,
    sigungu,
    programs,
    ordinances,
    sidoPrograms: sigungu ? support.programs.filter(p => inSido(p) && !p.sigungu) : [],
    sidoOrdinances: sigungu ? support.ordinances.filter(o => inSido(o) && !o.sigungu) : [],
  }
}

export const hasAnySupport = (r: RegionSupport) => r.programs.length + r.ordinances.length + r.sidoPrograms.length + r.sidoOrdinances.length > 0

/** 조례에 근거가 있는 지원 종류 (기본 책무만 있으면 basic) */
export function ordinanceKinds(list: SupportOrdinance[]): OrdinanceKind[] {
  const set = new Set<OrdinanceKind>()
  for (const o of list) for (const k of Object.keys(o.kinds) as OrdinanceKind[]) set.add(k)
  if (set.size > 1) set.delete('basic')
  return KIND_ORDER.filter(k => set.has(k))
}

export type SidoSummary = {
  sido: string
  /** 신청할 수 있는 지원 수 (시·도 + 시·군·구) */
  programs: number
  /** 조례로 입양비·진료비를 지원할 수 있는 지자체 수 */
  costPlaces: number
  insurancePlaces: number
  /** 입양 조문이 있는 지자체 수 */
  places: number
}

export function sidoSummary(sido: string): SidoSummary {
  const inSido = <T extends { sido: string | null }>(x: T) => normalizeSido(x.sido) === sido
  const ords = support.ordinances.filter(inSido)
  const placesOf = (kind?: OrdinanceKind) => new Set(ords.filter(o => !kind || o.kinds[kind]).map(o => o.sigungu ?? '')).size
  return { sido, programs: support.programs.filter(inSido).length, costPlaces: placesOf('cost'), insurancePlaces: placesOf('insurance'), places: placesOf() }
}

/** 시·도 안의 지역 목록 (시·도 자체는 sigungu=null) */
export function placesIn(sido: string): (string | null)[] {
  const inSido = <T extends { sido: string | null }>(x: T) => normalizeSido(x.sido) === sido
  const names = new Set<string | null>()
  for (const x of [...support.programs.filter(inSido), ...support.ordinances.filter(inSido)]) names.add(x.sigungu)
  return [...names].sort((a, b) => (a === null ? -1 : b === null ? 1 : a.localeCompare(b, 'ko')))
}

/** 검색어 → 지역 후보. '구리', '구리시', '경기 구리시', '부산' 모두 됨 */
export function searchRegions(query: string): { sido: string; sigungu: string | null }[] {
  const q = squash(query)
  if (!q) return []
  const tile = SIDOS.find(t => q === squash(t.sido) || q === t.short || q === t.short.replace('·', ''))
  if (tile) return [{ sido: tile.sido, sigungu: null }]
  const seen = new Map<string, { sido: string; sigungu: string | null }>()
  for (const x of [...support.programs, ...support.ordinances]) {
    const sido = normalizeSido(x.sido)
    if (!sido || !x.sigungu) continue
    const full = squash(sido + x.sigungu)
    const shortFull = squash((SIDOS.find(t => t.sido === sido)?.short ?? '') + x.sigungu)
    if (squash(x.sigungu).includes(q) || full.includes(q) || shortFull.includes(q)) seen.set(sido + x.sigungu, { sido, sigungu: x.sigungu })
  }
  return [...seen.values()]
}

/** 'YYYYMMDD' 또는 'YYYY-MM-DD' → 'YYYY.MM.DD' */
export function dotDate(d: string | null | undefined) {
  const s = (d ?? '').replace(/-/g, '')
  return s.length === 8 ? `${s.slice(0, 4)}.${s.slice(4, 6)}.${s.slice(6)}` : ''
}

/** 지역 대표 위치 */
export function regionCoord(sido: string, sigungu: string | null) {
  const s = normalizeSido(sido)
  return support.regions.find(r => normalizeSido(r.sido) === s && sameSigungu(r.sigungu, sigungu)) ?? null
}

export const regionId = (sido: string, sigungu: string | null) => `${sido}|${sigungu ?? ''}`
export function parseRegionId(id: string) {
  const [sido, sigungu] = id.split('|')
  return { sido, sigungu: sigungu || null }
}

/** 지도 표시용: 입양 지원이 있는 지역마다 한 점 */
export function supportPlaces() {
  return support.regions.map(r => {
    const s = supportFor(r.sido, r.sigungu)
    const kinds = ordinanceKinds(s.ordinances)
    return { ...r, id: regionId(r.sido, r.sigungu), programs: s.programs.length, kinds }
  }).filter(p => p.programs > 0 || p.kinds.length > 0)
}

export type FestivalPeriod = 'upcoming' | 'year' | 'all'

/** 기간별 반려동물 축제 (위치 있는 것만 지도에) */
export function festivalsBy(period: FestivalPeriod, today: string) {
  const year = today.slice(0, 4)
  return support.festivals.filter(f => period === 'all' || (period === 'year' ? f.start.startsWith(year) || f.end.startsWith(year) : (f.end || f.start) >= today))
}

/** 시·도별 개수 (많은 순) */
export function countBySido<T extends { sido: string | null }>(list: T[]) {
  const m = new Map<string, number>()
  for (const x of list) { const s = normalizeSido(x.sido) ?? '지역 미상'; m.set(s, (m.get(s) ?? 0) + 1) }
  return [...m.entries()].sort((a, b) => b[1] - a[1])
}

export const shortSido = (sido: string) => SIDOS.find(t => t.sido === sido)?.short ?? sido

/**
 * 입양하면 받을 수 있는 혜택 (확인된 것만): 그 시·군·구 + 속한 시·도.
 * 조례만 있고 확인되지 않은 지원은 보여 주지 않습니다(조례는 혜택 카드의 '근거'로만).
 */
export function benefitsFor(sidoName: string, sigungu: string | null) {
  const sido = normalizeSido(sidoName) ?? sidoName
  const inSido = (p: SupportProgram) => normalizeSido(p.sido) === sido
  return {
    sido,
    sigungu,
    local: sigungu ? support.programs.filter(p => inSido(p) && sameSigungu(p.sigungu, sigungu)) : [],
    sidoWide: support.programs.filter(p => inSido(p) && !p.sigungu),
  }
}

/** 확인된 혜택이 있는 시·군·구 (시·도 안) */
export function benefitPlacesIn(sido: string) {
  const s = normalizeSido(sido)
  return [...new Set(support.programs.filter(p => normalizeSido(p.sido) === s && p.sigungu).map(p => p.sigungu as string))].sort((a, b) => a.localeCompare(b, 'ko'))
}

/** 시·도별 확인된 혜택 수 (시·도 전체 + 시·군·구) */
export function benefitCountBySido() {
  const m = new Map<string, { total: number; sidoWide: number; places: Set<string> }>()
  for (const p of support.programs) {
    const s = normalizeSido(p.sido)
    if (!s) continue
    const v = m.get(s) ?? { total: 0, sidoWide: 0, places: new Set<string>() }
    v.total += 1
    if (p.sigungu) v.places.add(p.sigungu)
    else v.sidoWide += 1
    m.set(s, v)
  }
  return m
}

/** 혜택 이름 앞 꼬리표 */
export const programKindLabel = (p: SupportProgram) => (p.kinds.includes('cost') ? '입양비' : '펫보험')

/** 두 좌표 사이 거리 (km) */
export function distanceKm(a: { lat: number; lng: number }, b: { lat: number; lng: number }) {
  const R = 6371, rad = (d: number) => (d * Math.PI) / 180
  const h = Math.sin(rad(b.lat - a.lat) / 2) ** 2 + Math.cos(rad(a.lat)) * Math.cos(rad(b.lat)) * Math.sin(rad(b.lng - a.lng) / 2) ** 2
  return 2 * R * Math.asin(Math.sqrt(h))
}

/** 내 위치에서 가까운 확인된 혜택 (지역 대표 위치 기준, 가까운 순) */
export function nearestBenefits(me: { lat: number; lng: number }, limit = 3) {
  return support.programs
    .map(p => ({ p, c: regionCoord(normalizeSido(p.sido) ?? '', p.sigungu) }))
    .filter((x): x is { p: SupportProgram; c: NonNullable<ReturnType<typeof regionCoord>> } => x.c !== null)
    .map(x => ({ program: x.p, km: Math.round(distanceKm(me, x.c)) }))
    .sort((a, b) => a.km - b.km)
    .slice(0, limit)
}
