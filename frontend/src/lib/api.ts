import type { Animal, AnimalShelter, ShelterSummary, Species } from '@/types/animal'

/**
 * 검색 API 주소. 기본은 같은 주소의 /api (k3s·CloudFront 배포: /api 를 검색 API 로 넘김).
 * 로컬 개발은 frontend/.env.local 에 NEXT_PUBLIC_API_BASE_URL=http://localhost:8000 (.env.example 참고).
 */
export const API_BASE_URL = (process.env.NEXT_PUBLIC_API_BASE_URL || '/api').replace(/\/$/, '')

// ── 검색 API 응답 모양 (src/rebom/serving/schemas.py) ──
type ApiShelter = AnimalShelter
export type ApiAnimal = {
  desertionNo: string
  noticeNo: string | null
  species: string | null
  speciesCode: string | null
  breed: string | null
  color: string | null
  sex: 'M' | 'F' | 'Q' | null
  neutered: 'Y' | 'N' | 'U' | null
  ageText: string | null
  weightText: string | null
  ageYears: number | null
  ageLabel: string | null
  weightKg: number | null
  state: string | null
  isProtected: boolean
  specialMark: string | null
  thumbnail: string | null
  photos: string[]
  happenDate: string | null
  happenPlace: string | null
  noticeStart: string | null
  noticeEnd: string | null
  orgName: string | null
  shelter: ApiShelter
  updatedAt: string | null
  // 상세
  endReason?: string | null
  healthCheck?: string | null
  vaccinationCheck?: string | null
  sociality?: string | null
  healthNote?: string | null
}
export type ApiPage = { total: number; page: number; size: number; items: ApiAnimal[] }

export type AnimalSearchParams = {
  species?: Species
  state?: 'protect' | 'ended'
  sex?: 'M' | 'F' | 'Q'
  /** 시·도 단어 여러 개 (예: ['서울', '경기']) */
  region?: string[]
  /** 보호소 등록번호 */
  shelter?: string
  q?: string
  ageMin?: number
  ageMax?: number
  weightMin?: number
  weightLt?: number
  sort?: 'latest' | 'notice_end'
  page?: number
  size?: number
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message)
  }
}

async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  let res: Response
  try {
    res = await fetch(`${API_BASE_URL}${path}`, { signal, headers: { Accept: 'application/json' } })
  } catch (e) {
    if (e instanceof DOMException && e.name === 'AbortError') throw e
    throw new ApiError(0, '검색 서버에 연결할 수 없어요.')
  }
  if (!res.ok) throw new ApiError(res.status, res.status === 404 ? '찾는 공고가 없어요.' : `검색 서버 오류 (${res.status})`)
  return res.json() as Promise<T>
}

function toQuery(p: AnimalSearchParams) {
  const q = new URLSearchParams()
  const set = (key: string, value: string | number | undefined) => { if (value !== undefined && value !== '') q.set(key, String(value)) }
  set('species', p.species)
  set('state', p.state)
  set('sex', p.sex)
  set('region', p.region?.join(','))
  set('shelter', p.shelter)
  set('q', p.q?.trim())
  set('age_min', p.ageMin)
  set('age_max', p.ageMax)
  set('weight_min', p.weightMin)
  set('weight_lt', p.weightLt)
  set('sort', p.sort)
  set('page', p.page)
  set('size', p.size)
  return q.toString()
}

export async function fetchAnimals(params: AnimalSearchParams, signal?: AbortSignal) {
  const page = await getJson<ApiPage>(`/animals?${toQuery(params)}`, signal)
  return { total: page.total, items: page.items.map(toAnimal) }
}

export async function fetchAnimal(id: string, signal?: AbortSignal) {
  return toAnimal(await getJson<ApiAnimal>(`/animals/${encodeURIComponent(id)}`, signal))
}

export async function fetchShelters(signal?: AbortSignal) {
  return (await getJson<{ total: number; items: ShelterSummary[] }>('/shelters', signal)).items
}

// ── 응답 → 화면용 Animal ──
const SPECIES: Record<string, Species> = { '417000': 'dog', '422400': 'cat', '429900': 'etc' }
const GENDER = { M: '수컷', F: '암컷', Q: '성별 미상' } as const
const NEUTER = { Y: '중성화 완료', N: '중성화 안 함', U: '중성화 확인 필요' } as const

/** 색상 문구로 사진 없는 공고의 일러스트 색을 고릅니다. */
function artColors(color: string): Pick<Animal, 'coat' | 'ear' | 'bg'> {
  const c = color.replace(/\s/g, '')
  if (/^(검|흑|black)/i.test(c)) return { coat: '#494039', ear: '#302C28', bg: '#FAE3D1' }
  if (/^(흰|백|white)/i.test(c)) return { coat: '#FAF8F0', ear: '#EEE9DB', bg: '#DEE6D0' }
  if (/(치즈|노랑|황|레몬|오렌지)/.test(c)) return { coat: '#DCA76A', ear: '#C68D52', bg: '#E9EDDF' }
  if (/(크림|베이지|아이보리)/.test(c)) return { coat: '#EAD3AE', ear: '#D4B68A', bg: '#E9EDDD' }
  if (/(회|그레이|고등어|은)/.test(c)) return { coat: '#B9B4AA', ear: '#8F8A80', bg: '#E8E7DF' }
  if (/(초코|초콜|밤)/.test(c)) return { coat: '#96725A', ear: '#71523F', bg: '#E4E8D9' }
  return { coat: '#CA945F', ear: '#9B653A', bg: '#F7E7D9' }
}

export function toAnimal(a: ApiAnimal): Animal {
  const species = SPECIES[a.speciesCode ?? ''] ?? 'etc'
  const color = a.color || '색상 미기재'
  return {
    id: a.desertionNo,
    noticeNo: a.noticeNo,
    species,
    cat: species === 'cat',
    kind: a.breed || a.species || '품종 미상',
    gender: a.sex ? GENDER[a.sex] : '성별 미상',
    age: a.ageYears,
    ageLabel: a.ageLabel ?? '나이 미상',
    kg: a.weightKg,
    weightLabel: a.weightKg !== null ? `${a.weightKg}kg` : '체중 미상',
    color,
    care: a.neutered ? NEUTER[a.neutered] : NEUTER.U,
    state: a.state ?? '',
    protectedNow: a.isProtected,
    happenDate: a.happenDate,
    happenPlace: a.happenPlace,
    noticeStart: a.noticeStart,
    noticeEnd: a.noticeEnd,
    center: a.shelter.name || '보호소 미기재',
    shelter: a.shelter,
    quote: a.specialMark || '',
    photos: a.photos,
    healthCheck: a.healthCheck,
    vaccinationCheck: a.vaccinationCheck,
    sociality: a.sociality,
    healthNote: a.healthNote,
    endReason: a.endReason,
    ...artColors(color),
  }
}
