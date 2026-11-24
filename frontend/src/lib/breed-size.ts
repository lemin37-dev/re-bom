import { BREED_SIZES, type BreedSizeFamily, type BreedSizeLine } from '../data/breed-size'

/** Silver cleaning/weight.py size_class() 와 같은 경계: 10kg 미만 소형 · 25kg 미만 중형 · 이상 대형 (11-20 결정) */
const SMALL_LT = 10
const MEDIUM_LT = 25

export type BreedSizeView = {
  family: string
  /** 공고 품종명에 크기가 있으면 1줄, 계열 이름이면 모든 줄 */
  lines: { name: string; label: string }[]
  /** 크기가 여러 가지인 계열 전체를 보여 주는지 (줄마다 이름을 붙임) */
  showNames: boolean
  sources: string[]
}

function sizeOf(kg: number) {
  return kg < SMALL_LT ? '소형' : kg < MEDIUM_LT ? '중형' : '대형'
}

const kg = (v: number) => `${Number.isInteger(v) ? v : v.toString()}`

/** '1.8~2.7kg, 소형' · '4.5~11kg, 소형~중형' · '5kg 이하, 소형' · 자료가 없으면 null */
export function breedSizeLabel(line: Pick<BreedSizeLine, 'minKg' | 'maxKg'>): string | null {
  const { minKg, maxKg } = line
  if (maxKg === null) return null
  const low = sizeOf(minKg ?? maxKg)
  const high = sizeOf(maxKg)
  const size = low === high ? low : `${low}~${high}`
  const weight = minKg === null ? `${kg(maxKg)}kg 이하` : minKg === maxKg ? `${kg(maxKg)}kg` : `${kg(minKg)}~${kg(maxKg)}kg`
  return `${weight}, ${size}`
}

/** 공고 품종명 비교: 띄어쓰기, '져/저'·'쳐/처' 표기 차이를 무시 */
export function normalizeKind(name: string) {
  return name.replace(/\s+/g, '').replace(/져/g, '저').replace(/쳐/g, '처')
}

function view(f: BreedSizeFamily, lines: BreedSizeLine[], showNames: boolean): BreedSizeView | null {
  const shown = lines
    .map(l => ({ name: l.name, label: breedSizeLabel(l) ?? (showNames ? '몸무게 자료 없음' : null) }))
    .filter((l): l is { name: string; label: string } => l.label !== null)
  if (shown.length === 0 || shown.every(l => l.label === '몸무게 자료 없음')) return null
  const sources = [...new Set(lines.flatMap(l => l.sources))]
  return { family: f.family, lines: shown, showNames, sources }
}

/** 공고 품종명(kindNm) → 품종 정보(크기). 표에 없는 품종·믹스견은 null (화면에서 숨김) */
export function breedSizeOf(kind: string | null | undefined): BreedSizeView | null {
  if (!kind) return null
  const k = normalizeKind(kind)
  for (const f of BREED_SIZES) {
    const line = f.lines.find(l => l.kindNm.some(n => normalizeKind(n) === k))
    if (line) return view(f, [line], false)
    if (f.familyKindNm.some(n => normalizeKind(n) === k)) return view(f, f.lines, f.lines.length > 1)
  }
  return null
}
