'use client'

import { AnimalArt } from '@/components/animal/AnimalArt'
import { Icon } from '@/components/icons/Icon'
import { IllustratedMap } from '@/components/map/IllustratedMap'
import type { Animal } from '@/types/animal'

/** 웹 검색 결과 지도 보기: 예시 지도 + 가까운 순 목록 */
export function ResultMap({ animals, onOpen }: { animals: Animal[]; onOpen: (a: Animal) => void }) {
  const nearest = [...animals].sort((a, b) => a.distance - b.distance).slice(0, 4)
  return <div className="map-layout">
    <IllustratedMap>
      {animals.slice(0, 8).map((a, i) => <button key={a.id} className={`map-pin pin-${i}`} onClick={() => onOpen(a)}><Icon name="pin" size={16} />{a.center.split(' ')[0]}<b>{a.score}%</b></button>)}
      <div className="map-note">보호센터 위치를 설명하기 위한 예시 지도예요.</div>
    </IllustratedMap>
    <aside className="map-aside"><h3>가까운 곳에서 만나요</h3><p>방문 전 보호센터에 꼭 확인해 주세요.</p>
      {nearest.map(a => <button className="map-list-item" key={a.id} onClick={() => onOpen(a)}><div style={{ background: a.bg }}><AnimalArt animal={a} small /></div><span><strong>{a.kind} · {a.age}살</strong><small>{a.center}<br />{a.distance}km</small></span><Icon name="arrow" size={16} /></button>)}
    </aside>
  </div>
}
