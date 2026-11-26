import type { Animal, AnimalShelter, ShelterSummary, SizeLabel, Species } from '@/types/animal'
import type { PhotoTag, TagCatalog, TagMatch, TagSpecies } from '@/types/tags'

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
  /** Silver birth_year. 검색 API에 아직 없을 수 있음 → 없으면 ageText 의 연도를 씀 */
  birthYear?: number | null
  /** Silver weight_kg: 범위는 큰 값, g→kg, 이상치 null */
  weightKg: number | null
  /** Silver size: 개만. 10kg 미만 small · 25kg 미만 medium · 이상 large */
  size: 'small' | 'medium' | 'large' | null
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
  /** 대표 사진을 보고 모델이 붙인 태그. 태그가 없는 공고는 빈 배열 */
  photoTags?: PhotoTag[]
  /** 태그를 골라 검색했을 때만 옴 */
  tagMatch?: TagMatch | null
  // 상세
  endReason?: string | null
  healthCheck?: string | null
  vaccinationCheck?: string | null
  sociality?: string | null
  healthNote?: string | null
}
/** matched: 태그를 골랐을 때 고른 범주가 모두 맞는 아이 수 (목록 맨 앞) */
export type ApiPage = { total: number; matched?: number | null; page: number; size: number; items: ApiAnimal[] }

export type AnimalSearchParams = {
  species?: Species
  state?: 'protect' | 'ended'
  sex?: 'M' | 'F' | 'Q'
  /** 시·도 단어 여러 개 (예: ['서울', '경기']) */
  region?: string[]
  /** 보호소 등록번호 */
  shelter?: string
  q?: string
  weightMin?: number
  weightLt?: number
  /** 사진 태그 코드 (예: ['ear.drop', 'fur.fluffy']). 같은 범주 안은 하나라도, 범주끼리는 모두 맞아야 함 */
  tags?: string[]
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

/** 사진 파일을 본문 그대로 보낸다 (POST /animals/photo-search). 422 는 서버가 사진 문제를 문장으로 알려 준다. */
async function postPhoto<T>(path: string, photo: File, signal?: AbortSignal): Promise<T> {
  let res: Response
  try {
    res = await fetch(`${API_BASE_URL}${path}`, {
      method: 'POST', body: photo, signal,
      headers: { Accept: 'application/json', 'Content-Type': photo.type || 'application/octet-stream' },
    })
  } catch (e) {
    if (e instanceof DOMException && e.name === 'AbortError') throw e
    throw new ApiError(0, '검색 서버에 연결할 수 없어요.')
  }
  if (res.status === 422) {
    const detail = await res.json().then(b => (typeof b?.detail === 'string' ? b.detail : null)).catch(() => null)
    throw new ApiError(422, detail ?? '이 사진으로는 찾을 수 없어요.')
  }
  if (!res.ok) throw new ApiError(res.status, `검색 서버 오류 (${res.status})`)
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
  set('text', p.q?.trim()) // 검색창 문장은 글 벡터와 비교한다(API text). sample 모드 API 는 단어 포함으로 거른다
  set('weight_min', p.weightMin)
  set('weight_lt', p.weightLt)
  set('tags', p.tags?.join(','))
  set('sort', p.sort)
  set('page', p.page)
  set('size', p.size)
  return q.toString()
}

/** photo 가 있으면 사진 벡터로 줄 세운다(문장은 쓰지 않음, API 는 벡터 하나로만 줄 세움). 없으면 문장은 글 벡터로. */
export async function fetchAnimals(params: AnimalSearchParams, signal?: AbortSignal, photo?: File | null) {
  const page = photo
    ? await postPhoto<ApiPage>(`/animals/photo-search?${toQuery({ ...params, q: undefined })}`, photo, signal)
    : await getJson<ApiPage>(`/animals?${toQuery(params)}`, signal)
  return { total: page.total, matched: page.matched ?? null, items: page.items.map(toAnimal) }
}

/** 지도용: 목록과 같은 조건의 보호소별 마릿수 (GET /animals/places). 문장·사진은 거르지 않아 마릿수에 영향 없음 */
export type PlaceCount = { regNo: string; count: number }
export async function fetchPlaces(params: AnimalSearchParams, signal?: AbortSignal) {
  return getJson<{ total: number; items: PlaceCount[] }>(`/animals/places?${toQuery({ ...params, page: undefined, size: undefined, sort: undefined })}`, signal)
}

export async function fetchAnimal(id: string, signal?: AbortSignal) {
  return toAnimal(await getJson<ApiAnimal>(`/animals/${encodeURIComponent(id)}`, signal))
}

export async function fetchTagCatalog(species: TagSpecies, signal?: AbortSignal) {
  return getJson<TagCatalog>(`/tags?species=${species}`, signal)
}

export async function fetchShelters(signal?: AbortSignal) {
  return (await getJson<{ total: number; items: ShelterSummary[] }>('/shelters', signal)).items
}

// ── 응답 → 화면용 Animal ──
const SPECIES: Record<string, Species> = { '417000': 'dog', '422400': 'cat', '429900': 'etc' }
const GENDER = { M: '수컷', F: '암컷', Q: '성별 미상' } as const
const NEUTER = { Y: '중성화 완료', N: '중성화 안 함', U: '중성화 확인 필요' } as const
/** Silver size 는 영어 코드로 저장됨 → 화면은 한국어 (Silver 쪽 표기 합의 전까지 여기서 바꿈) */
const SIZE: Record<string, SizeLabel> = { small: '소형', medium: '중형', large: '대형' }

/** 원문 '2022(년생)' → 2022. 검색 API가 birthYear 를 주기 전까지 쓰는 대체값 */
function birthYearOf(ageText: string | null) {
  const m = /^(\d{4})/.exec(ageText?.trim() ?? '')
  return m ? Number(m[1]) : null
}

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
    birthYear: a.birthYear ?? birthYearOf(a.ageText),
    kg: a.weightKg,
    weightLabel: a.weightKg !== null ? `${a.weightKg}kg` : '체중 미상',
    size: SIZE[a.size ?? ''] ?? null,
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
    photoTags: a.photoTags ?? [],
    tagMatch: a.tagMatch ?? null,
    healthCheck: a.healthCheck,
    vaccinationCheck: a.vaccinationCheck,
    sociality: a.sociality,
    healthNote: a.healthNote,
    endReason: a.endReason,
    ...artColors(color),
  }
}
