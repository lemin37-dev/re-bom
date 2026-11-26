'use client'

import { useEffect, useRef, useState } from 'react'
import { type KakaoMap, type KakaoNs, type KakaoOverlay, loadKakao } from '@/lib/kakao'

/**
 * 한반도 지도. 카카오 지도 키(NEXT_PUBLIC_KAKAO_JS_KEY)가 있으면 카카오 지도, 없거나 못 불러오면 OpenStreetMap(Leaflet).
 * 화면은 아래 props 만 씁니다. 지도 제공처를 바꿀 때는 이 파일의 '엔진'만 바꿉니다.
 */
export type MapMarker = {
  id: string
  lat: number
  lng: number
  /** 원 크기 (px) */
  radius: number
  tone: 'forest' | 'sage' | 'peach' | 'muted'
  /** 마우스를 올리면 보이는 글 */
  tooltip: string
  /** 원 안에 숫자 등 */
  label?: string
  /** true 면 원 대신 글자 길이에 맞춘 알약 모양 (예: '경기 23') */
  pill?: boolean
}
type Point = { lat: number; lng: number }
type Focus = { lat: number; lng: number; zoom: number } | null

/** 지도 엔진: 카카오 · Leaflet 이 같은 일을 하도록 */
type Engine = {
  setMarkers(markers: MapMarker[], selectedId: string | null | undefined, onSelect: (id: string) => void): void
  setMe(me: Point | null): void
  focus(f: Focus): void
  destroy(): void
}

const TONE = { forest: '#3f5233', sage: '#8fa978', peach: '#e3936a', muted: '#b9bfae' }
const TILE_URL = process.env.NEXT_PUBLIC_MAP_TILE_URL || 'https://tile.openstreetmap.org/{z}/{x}/{y}.png'
const ATTRIBUTION = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
const KOREA: [[number, number], [number, number]] = [[33.1, 125.6], [38.45, 129.6]]

/** 점 하나의 HTML (두 엔진 공통) */
function markerElement(mk: MapMarker, selected: boolean, onSelect: (id: string) => void) {
  const el = document.createElement('span')
  const size = mk.radius * 2
  el.className = `${mk.pill ? 'map-pill' : 'map-dot'} tone-${mk.tone}${selected ? ' selected' : ''}`
  el.style.background = TONE[mk.tone]
  if (mk.pill) el.style.fontSize = `${Math.max(10, Math.min(14, mk.radius))}px`
  else { el.style.width = `${size}px`; el.style.height = `${size}px` }
  el.textContent = mk.label ?? ''
  el.title = mk.tooltip
  el.setAttribute('role', 'button')
  el.setAttribute('aria-label', mk.tooltip)
  el.tabIndex = 0
  el.addEventListener('click', () => onSelect(mk.id))
  el.addEventListener('keydown', e => { if (e.key === 'Enter') onSelect(mk.id) })
  return el
}

function meElement() {
  const el = document.createElement('span')
  el.className = 'map-me'
  el.title = '내 위치'
  return el
}

/** Leaflet zoom ↔ 카카오 level (대략) */
const kakaoLevel = (zoom: number) => Math.max(1, Math.min(14, Math.round(19.5 - zoom)))

function kakaoEngine(k: KakaoNs, box: HTMLElement): Engine {
  const m = k.maps
  const map: KakaoMap = new m.Map(box, { center: new m.LatLng(36.0, 127.8), level: 13, scrollwheel: true })
  map.addControl(new m.ZoomControl(), m.ControlPosition.RIGHT)
  // 전국: 남한이 화면을 채우도록 (setBounds 는 한 단계 멀리 잡혀 직접 맞춤)
  const showKorea = () => { map.setLevel(13); map.setCenter(new m.LatLng(35.85, 127.75)) }
  showKorea()
  let overlays: KakaoOverlay[] = []
  let meOverlay: KakaoOverlay | null = null
  return {
    setMarkers(markers, selectedId, onSelect) {
      overlays.forEach(o => o.setMap(null))
      overlays = markers.map(mk => {
        const o = new m.CustomOverlay({ position: new m.LatLng(mk.lat, mk.lng), content: markerElement(mk, mk.id === selectedId, onSelect), xAnchor: 0.5, yAnchor: 0.5, zIndex: mk.id === selectedId ? 10 : 1, clickable: true })
        o.setMap(map)
        return o
      })
    },
    setMe(me) {
      meOverlay?.setMap(null)
      meOverlay = me ? new m.CustomOverlay({ position: new m.LatLng(me.lat, me.lng), content: meElement(), xAnchor: 0.5, yAnchor: 0.5, zIndex: 20 }) : null
      meOverlay?.setMap(map)
    },
    focus(f) {
      map.relayout()
      if (!f) { showKorea(); return }
      // 중심을 먼저 옮기고 확대한다 (확대 애니메이션 중의 panTo 는 무시돼 먼 곳으로 못 감)
      map.setCenter(new m.LatLng(f.lat, f.lng))
      map.setLevel(kakaoLevel(f.zoom), { animate: true })
    },
    destroy() { overlays.forEach(o => o.setMap(null)); meOverlay?.setMap(null); box.innerHTML = '' },
  }
}

async function leafletEngine(box: HTMLElement): Promise<Engine> {
  const L = await import('leaflet')
  const map = L.map(box, { zoomControl: true, scrollWheelZoom: true, attributionControl: true, minZoom: 6, maxZoom: 13, zoomSnap: 0.25 })
  L.tileLayer(TILE_URL, { attribution: ATTRIBUTION, maxZoom: 18 }).addTo(map)
  map.fitBounds(KOREA)
  const layer = L.layerGroup().addTo(map)
  const meLayer = L.layerGroup().addTo(map)
  const icon = (el: HTMLElement, mk?: MapMarker) => L.divIcon({ className: '', html: el, iconSize: mk && !mk.pill ? [mk.radius * 2, mk.radius * 2] : [0, 0], iconAnchor: mk && !mk.pill ? [mk.radius, mk.radius] : [0, 0] })
  return {
    setMarkers(markers, selectedId, onSelect) {
      layer.clearLayers()
      for (const mk of markers) L.marker([mk.lat, mk.lng], { icon: icon(markerElement(mk, mk.id === selectedId, onSelect), mk), keyboard: false, zIndexOffset: mk.id === selectedId ? 1000 : mk.radius }).addTo(layer)
    },
    setMe(me) {
      meLayer.clearLayers()
      if (me) L.marker([me.lat, me.lng], { icon: L.divIcon({ className: '', html: meElement(), iconSize: [18, 18], iconAnchor: [9, 9] }), zIndexOffset: 2000 }).addTo(meLayer)
    },
    focus(f) {
      if (f) map.flyTo([f.lat, f.lng], f.zoom, { duration: 0.6 })
      else map.flyToBounds(KOREA, { duration: 0.6 })
    },
    destroy() { map.remove() },
  }
}

export function KoreaMap({ markers, selectedId, onSelect, focus, me = null, height = 520, label }: {
  markers: MapMarker[]
  selectedId?: string | null
  onSelect?: (id: string) => void
  /** 이 위치로 이동 (null 이면 전국) */
  focus?: Focus
  /** 내 위치 (파란 점) */
  me?: Point | null
  height?: number
  label: string
}) {
  const box = useRef<HTMLDivElement>(null)
  const [engine, setEngine] = useState<Engine | null>(null)
  const [provider, setProvider] = useState<'kakao' | 'osm' | null>(null)
  const select = useRef(onSelect)
  useEffect(() => { select.current = onSelect }, [onSelect])

  // 지도 만들기 (브라우저에서만): 카카오 → 안 되면 Leaflet
  useEffect(() => {
    let cancelled = false
    let made: Engine | null = null
    ;(async () => {
      const k = await loadKakao()
      if (cancelled || !box.current) return
      made = k ? kakaoEngine(k, box.current) : await leafletEngine(box.current)
      if (cancelled) { made.destroy(); return }
      setProvider(k ? 'kakao' : 'osm')
      setEngine(made)
    })()
    return () => { cancelled = true; made?.destroy() }
  }, [])

  useEffect(() => { engine?.setMarkers(markers, selectedId, id => select.current?.(id)) }, [engine, markers, selectedId])
  useEffect(() => { engine?.setMe(me) }, [engine, me])
  useEffect(() => { engine?.focus(focus ?? null) }, [engine, focus])

  return <div className={`korea-map${provider ? ` provider-${provider}` : ''}`} ref={box} style={{ height }} role="region" aria-label={label} />
}
