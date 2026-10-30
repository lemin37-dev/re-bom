'use client'

import { useState } from 'react'
import { Icon } from '@/components/icons/Icon'
import { IllustratedMap } from '@/components/map/IllustratedMap'
import { regions, shelters } from '@/data/shelters'

/** 보호센터 화면 (웹·앱 공통 본문). 앱(compact)에서는 목록/지도 전환 버튼을 추가로 보여 줍니다. */
export function ShelterDirectory({ onFind, compact = false }: { onFind: (center: string) => void; compact?: boolean }) {
  const [mapView, setMapView] = useState(false)
  const [region, setRegion] = useState('전체 지역')
  const [type, setType] = useState('전체')
  const [selected, setSelected] = useState(shelters[0].name)
  const list = shelters.slice(0, 8).filter(s => (region === '전체 지역' || s.region === region) && (type === '전체' || s.type === type))
  const current = list.find(s => s.name === selected) || list[0]

  return <section className="directory-page" data-view={mapView ? 'map' : 'list'}>
    <div className="page-heading"><div><div className="eyebrow"><Icon name="pin" size={15} />새로운 인연이 시작되는 곳</div><h1>친구들이 머무는 보호센터</h1><p>가까운 보호센터를 찾고, 그곳에서 기다리는 아이들을 만나 보세요.</p></div></div>
    <div className="directory-filters">
      <label>지역<select value={region} onChange={e => setRegion(e.target.value)}><option>전체 지역</option>{Object.keys(regions).map(r => <option key={r}>{r}</option>)}</select></label>
      <fieldset><legend>운영 형태</legend>{['전체', '직영', '위탁'].map(t => <button key={t} className={type === t ? 'selected' : ''} onClick={() => setType(t)} aria-pressed={type === t}>{t}</button>)}</fieldset>
      <span>보호센터 <strong>{list.length}</strong>곳</span>
    </div>
    {compact && <div className="mobile-directory-view" role="group" aria-label="보호센터 보기 방식"><button className={!mapView ? 'selected' : ''} aria-pressed={!mapView} onClick={() => setMapView(false)}><Icon name="grid" size={17} />목록으로</button><button className={mapView ? 'selected' : ''} aria-pressed={mapView} onClick={() => setMapView(true)}><Icon name="pin" size={17} />지도로</button></div>}
    <div className="directory-layout">
      <div className="directory-list">{list.map(s => <article key={s.name} className={`directory-card ${current?.name === s.name ? 'selected-center' : ''}`}>
        <div className="directory-card-top"><span className="operation-badge">{s.type} 운영</span><span>{s.region} · {s.distance}km</span></div>
        <button className="center-name" onClick={() => setSelected(s.name)} aria-pressed={current?.name === s.name}>{s.name}<Icon name="pin" size={17} /></button>
        <p>{s.address}</p><p>전화 {s.phone} <small>예시 번호</small></p>
        <div className="directory-card-bottom"><span>보호 중인 친구 <b>{s.count}마리</b></span><button onClick={() => onFind(s.name)}>이 센터 아이들 보기<Icon name="arrow" size={15} /></button></div>
      </article>)}</div>
      <aside className="directory-map">
        <IllustratedMap>
          {list.map((s, i) => <button key={s.name} className={`map-pin pin-${i} ${current?.name === s.name ? 'active-pin' : ''}`} onClick={() => setSelected(s.name)} aria-pressed={current?.name === s.name}><Icon name="pin" size={16} />{s.name.split(' ')[0]}</button>)}
          <span className="map-placeholder-label">지도 자리표시</span>
        </IllustratedMap>
        {current && <div className="selected-center-info"><span className="soft-icon"><Icon name="pin" size={22} /></span><div><strong>{current.name}</strong><p>{current.address}</p><small>{current.hours}</small></div><button className="icon-button" onClick={() => onFind(current.name)} aria-label={`${current.name}의 아이들 보기`}><Icon name="arrow" /></button></div>}
        <p className="reference-note">지도·센터명·주소·전화·거리·동물 수는 모두 가상 데이터입니다.</p>
      </aside>
    </div>
  </section>
}
