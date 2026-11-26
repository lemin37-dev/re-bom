/**
 * 카카오 지도 JavaScript SDK. 키는 NEXT_PUBLIC_KAKAO_JS_KEY (frontend/.env.local).
 * - 지도(components/map/KoreaMap)와 위치 → 시·군·구 찾기에 씁니다. 나중에 보호센터 위치·길찾기도 여기서.
 * - 키가 없거나 SDK를 못 불러오면 null 을 돌려주고, 지도는 OpenStreetMap(Leaflet)으로, 위치는 '가까운 혜택 지역'으로 대신합니다.
 * - 카카오 개발자 콘솔에서 카카오맵 사용 설정(OPEN_MAP_AND_LOCAL)과 Web 사이트 도메인 등록이 필요합니다.
 */

type KakaoRegion = { region_type: string; region_1depth_name: string; region_2depth_name: string }

export type KakaoLatLng = { getLat(): number; getLng(): number }
export type KakaoMap = {
  setLevel(level: number, opt?: { animate?: boolean }): void
  panTo(p: KakaoLatLng): void
  setCenter(p: KakaoLatLng): void
  addControl(c: unknown, pos: unknown): void
  relayout(): void
}
export type KakaoOverlay = { setMap(map: KakaoMap | null): void; setZIndex(z: number): void }

export type KakaoNs = {
  maps: {
    load(cb: () => void): void
    LatLng: new (lat: number, lng: number) => KakaoLatLng
    Map: new (el: HTMLElement, opt: { center: KakaoLatLng; level: number; scrollwheel?: boolean }) => KakaoMap
    Circle: new (opt: { center: KakaoLatLng; radius: number; strokeWeight?: number; strokeColor?: string; strokeOpacity?: number; fillColor?: string; fillOpacity?: number }) => KakaoOverlay
    CustomOverlay: new (opt: { position: KakaoLatLng; content: HTMLElement; xAnchor?: number; yAnchor?: number; zIndex?: number; clickable?: boolean }) => KakaoOverlay
    ZoomControl: new () => unknown
    ControlPosition: { RIGHT: unknown; TOPLEFT: unknown }
    services: {
      Geocoder: new () => { coord2RegionCode(lng: number, lat: number, cb: (res: KakaoRegion[], status: string) => void): void }
      Status: { OK: string }
    }
  }
}

const KEY = process.env.NEXT_PUBLIC_KAKAO_JS_KEY
let loading: Promise<KakaoNs | null> | null = null

export const kakaoEnabled = () => Boolean(KEY)

export function loadKakao(): Promise<KakaoNs | null> {
  if (!KEY || typeof window === 'undefined') return Promise.resolve(null)
  loading ??= new Promise(resolve => {
    const win = window as unknown as { kakao?: KakaoNs }
    const done = () => (win.kakao?.maps ? win.kakao.maps.load(() => resolve(win.kakao!)) : resolve(null))
    if (win.kakao?.maps) return done()
    const s = document.createElement('script')
    s.src = `https://dapi.kakao.com/v2/maps/sdk.js?autoload=false&libraries=services&appkey=${encodeURIComponent(KEY)}`
    s.async = true
    s.onload = done
    s.onerror = () => resolve(null)
    document.head.appendChild(s)
    setTimeout(() => resolve(null), 8000)
  })
  return loading
}

/** 좌표 → { 시·도, 시·군·구 }. '수원시 장안구'처럼 구가 붙으면 시 이름만 씁니다. 못 찾으면 null */
export async function regionOfCoord(lat: number, lng: number): Promise<{ sido: string; sigungu: string | null } | null> {
  const k = await loadKakao()
  if (!k) return null
  return new Promise(resolve => {
    new k.maps.services.Geocoder().coord2RegionCode(lng, lat, (res, status) => {
      const r = status === k.maps.services.Status.OK ? (res.find(x => x.region_type === 'H') ?? res[0]) : null
      resolve(r ? { sido: r.region_1depth_name, sigungu: r.region_2depth_name ? r.region_2depth_name.split(' ')[0] : null } : null)
    })
    setTimeout(() => resolve(null), 6000)
  })
}
