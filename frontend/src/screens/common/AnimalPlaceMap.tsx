'use client'

import { useMemo, useState } from 'react'
import { AnimalPhoto } from '@/components/animal/AnimalPhoto'
import { Icon } from '@/components/icons/Icon'
import { KoreaMap } from '@/components/map/KoreaMap'
import { animalTitle } from '@/lib/animal'
import { distanceKm, distanceLabel, groupByPlace, type Place, placeMarker, type Point, readMyLocation } from '@/lib/shelter-map'
import type { Animal } from '@/types/animal'

type Locating = 'idle' | 'busy' | 'denied'

/** 이 장소(같은 건물의 보호소들)의 대표 이름: 가장 많이 나온 보호소 이름 */
function placeName(p: Place<Animal>) {
  const n = new Map<string, number>()
  for (const a of p.items) n.set(a.center, (n.get(a.center) ?? 0) + 1)
  return [...n.entries()].sort((x, y) => y[1] - x[1])[0][0]
}

/**
 * 검색 결과 지도 보기 (웹·앱 공통). 지금까지 불러온 아이들을 보호소 위치별로 묶어 점으로 보여 줍니다.
 * 점을 누르면 그 보호소의 아이들, '내 위치'를 누르면 가까운 보호소 순. 내 위치는 화면에서만 쓰고 저장하지 않습니다.
 */
export function AnimalPlaceMap({ animals, total, onOpen, compact = false }: { animals: Animal[]; total: number; onOpen: (a: Animal) => void; compact?: boolean }) {
  const [me, setMe] = useState<Point | null>(null)
  const [locating, setLocating] = useState<Locating>('idle')
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const { places, unplaced } = useMemo(() => groupByPlace(animals, a => a.shelter.regNo), [animals])
  const ordered = useMemo(() => [...places].sort(me
    ? (a, b) => distanceKm(me, a) - distanceKm(me, b)
    : (a, b) => b.items.length - a.items.length), [places, me])
  const selected = places.find(p => p.id === selectedId) ?? null
  const markers = useMemo(() => places.map(p => placeMarker(p.id, p.items.length, `${placeName(p)} · ${p.items.length}마리${p.precision === 'region' ? ' (대략 위치)' : ''}`, 'forest')), [places])
  const focus = useMemo(() => selected ? { lat: selected.lat, lng: selected.lng, zoom: 12 } : me ? { ...me, zoom: 10 } : null, [selected, me])

  async function locate() {
    setLocating('busy')
    try { setMe(await readMyLocation()); setSelectedId(null); setLocating('idle') } catch { setLocating('denied') }
  }

  return <div className={`place-map-layout${compact ? ' compact' : ''}`}>
    <div className="place-map-main">
      <KoreaMap label="검색 결과 보호센터 지도" markers={markers} selectedId={selectedId} onSelect={setSelectedId} me={me} focus={focus} height={compact ? 320 : 520} />
      <div className="place-map-tools">
        <button type="button" className="locate-button" onClick={locate} disabled={locating === 'busy'}><Icon name="pin" size={15} />{locating === 'busy' ? '위치를 찾는 중…' : me ? '내 위치 다시 찾기' : '내 위치에서 가까운 순'}</button>
        <span>지금 불러온 {animals.length}마리 기준{total > animals.length ? ` · 전체 ${total}마리` : ''}</span>
      </div>
      {locating === 'denied' && <p className="locate-status">위치를 가져오지 못했어요. 브라우저의 위치 권한을 확인해 주세요.</p>}
    </div>
    <aside className="place-map-aside">
      {selected
        ? <>
          <button type="button" className="back-link" onClick={() => setSelectedId(null)}><Icon name="arrow" size={14} />보호센터 목록</button>
          <h3>{placeName(selected)}</h3>
          <p>{selected.items[0].shelter.addr}{me && ` · ${distanceLabel(distanceKm(me, selected))}`}{selected.precision === 'region' && ' · 지도 위치는 대략이에요'}</p>
          {selected.items.map(a => <button className="map-list-item" key={a.id} onClick={() => onOpen(a)}><div style={{ background: a.bg }}><AnimalPhoto animal={a} small /></div><span><strong>{animalTitle(a)}</strong><small>{a.weightLabel} · {a.color}</small></span><Icon name="arrow" size={16} /></button>)}
        </>
        : <>
          <h3>{me ? '가까운 보호센터' : '이 친구들이 기다려요'}</h3>
          <p>점을 누르면 그 보호센터의 친구들을 볼 수 있어요. 방문 전 보호센터에 꼭 확인해 주세요.</p>
          {ordered.map(p => <button className="place-row" key={p.id} onClick={() => setSelectedId(p.id)}><span><strong>{placeName(p)}</strong><small>{p.items[0].shelter.sido} {p.items[0].shelter.sigungu}{me && ` · ${distanceLabel(distanceKm(me, p))}`}</small></span><b>{p.items.length}마리</b><Icon name="arrow" size={15} /></button>)}
          {unplaced.length > 0 && <p className="place-map-note">위치를 모르는 보호센터의 {unplaced.length}마리는 목록으로 보기에서 볼 수 있어요.</p>}
        </>}
    </aside>
  </div>
}
