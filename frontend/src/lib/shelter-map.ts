import snapshot from '@/data/shelter-coords.json'
import type { MapMarker } from '@/components/map/KoreaMap'

/**
 * 보호소 좌표 (scripts/shelter/build_shelter_coords.py 가 Silver 보호소 주소를 카카오로 바꾼 스냅샷).
 * 같은 건물을 시·군마다 따로 등록한 보호소가 많아서(예: 양주 한국동물구조관리협회 26곳) 지도는 좌표가 같은 보호소를 '장소' 하나로 묶습니다.
 * 나중에(B) 검색 API /shelters 가 lat·lng 를 주면 이 파일의 shelterPoint 만 바꿉니다.
 */
export type Precision = 'address' | 'place' | 'region'
export type Point = { lat: number; lng: number }
export type ShelterPoint = Point & { precision: Precision }

const COORDS = snapshot.shelters as unknown as Record<string, [number, number, Precision]>
export const SHELTER_COORDS_AT = snapshot.generatedAt.slice(0, 10)

export function shelterPoint(regNo: string | null | undefined): ShelterPoint | null {
  const c = regNo ? COORDS[regNo] : undefined
  return c ? { lat: c[0], lng: c[1], precision: c[2] } : null
}

export const placeId = (p: Point) => `${p.lat.toFixed(4)},${p.lng.toFixed(4)}`

/** 한 장소에 모인 것들 (보호소 · 동물). 좌표가 없는 것은 빠집니다 */
export type Place<T> = ShelterPoint & { id: string; items: T[] }

export function groupByPlace<T>(items: T[], regNoOf: (item: T) => string | null | undefined): { places: Place<T>[]; unplaced: T[] } {
  const byId = new Map<string, Place<T>>()
  const unplaced: T[] = []
  for (const item of items) {
    const p = shelterPoint(regNoOf(item))
    if (!p) { unplaced.push(item); continue }
    const id = placeId(p)
    const place = byId.get(id) ?? { ...p, id, items: [] }
    place.items.push(item)
    byId.set(id, place)
  }
  return { places: [...byId.values()], unplaced }
}

/** 두 지점 사이 거리 (km, 직선) */
export function distanceKm(a: Point, b: Point) {
  const rad = (d: number) => d * Math.PI / 180
  const h = Math.sin(rad(b.lat - a.lat) / 2) ** 2 + Math.cos(rad(a.lat)) * Math.cos(rad(b.lat)) * Math.sin(rad(b.lng - a.lng) / 2) ** 2
  return 6371 * 2 * Math.asin(Math.sqrt(h))
}

export function distanceLabel(km: number) {
  return km < 1 ? `${Math.round(km * 1000)}m` : `${km < 10 ? km.toFixed(1) : Math.round(km)}km`
}

/** 장소 점: 숫자(마릿수)가 클수록 크게. 선택한 장소는 진하게 */
export function placeMarker(id: string, count: number, tooltip: string, tone: MapMarker['tone'] = 'sage'): MapMarker {
  return { id, lat: Number(id.split(',')[0]), lng: Number(id.split(',')[1]), radius: Math.min(24, 11 + Math.sqrt(count) * 1.4), tone: count ? tone : 'muted', tooltip, label: count ? String(count) : '' }
}

/** 위치를 받지 못했을 때 가까운 순의 기준: 서울 강남구청 */
export const DEFAULT_BASE: Point & { label: string } = { lat: 37.51735, lng: 127.04739, label: '서울 강남구' }

/** 내 위치: 브라우저 위치 권한을 받아 한 번 읽음(accuracy: 오차 m). 화면에서만 쓰고 저장·전송하지 않음 */
export function readMyLocation(): Promise<Point & { accuracy: number }> {
  return new Promise((resolve, reject) => {
    if (typeof navigator === 'undefined' || !navigator.geolocation) { reject(new Error('unsupported')); return }
    navigator.geolocation.getCurrentPosition(
      pos => resolve({ lat: pos.coords.latitude, lng: pos.coords.longitude, accuracy: Math.round(pos.coords.accuracy || 0) }),
      reject,
      { enableHighAccuracy: false, timeout: 10000, maximumAge: 600000 },
    )
  })
}
