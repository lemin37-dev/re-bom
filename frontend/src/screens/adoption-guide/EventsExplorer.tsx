'use client'

import { useMemo, useState } from 'react'
import { KoreaMap, type MapMarker } from '@/components/map/KoreaMap'
import { Icon } from '@/components/icons/Icon'
import { adoptionEvents } from '@/data/adoption-guide'
import { type FestivalPeriod, countBySido, dotDate, festivalsBy, normalizeSido, regionCoord, shortSido, support } from '@/lib/adoption-support'

const PERIODS: { key: FestivalPeriod; label: string }[] = [
  { key: 'upcoming', label: '앞으로 열리는' },
  { key: 'year', label: '올해 전체' },
  { key: 'all', label: '모든 기간' },
]

function today() {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

type Item = { id: string; kind: 'adoption' | 'festival'; name: string; start: string; end: string; sido: string | null; sigungu: string | null; place: string; host: string; lat: number | null; lng: number | null; note: string }

/** 전국 입양 행사 · 반려동물 축제 (/guide/events): 지도 + 시·도별 개수 + 목록 */
export function EventsExplorer() {
  const now = useMemo(() => today(), [])
  const [period, setPeriod] = useState<FestivalPeriod>('year')
  const [sido, setSido] = useState<string | null>(null)
  const [selected, setSelected] = useState<string | null>(null)

  const items: Item[] = useMemo(() => {
    const inPeriod = (start: string, end: string) => period === 'all' || (period === 'year' ? start.startsWith(now.slice(0, 4)) || end.startsWith(now.slice(0, 4)) : (end || start) >= now)
    const events = adoptionEvents.filter(e => inPeriod(e.start, e.end)).map((e, i): Item => {
      const c = regionCoord(e.sido, e.sigungu) ?? regionCoord(e.sido, null)
      return { id: `a${i}`, kind: 'adoption', name: e.title, start: e.start, end: e.end, sido: e.sido, sigungu: e.sigungu, place: e.place, host: e.host, lat: c?.lat ?? null, lng: c?.lng ?? null, note: `운영팀 확인 ${dotDate(e.checkedAt)}` }
    })
    const festivals = festivalsBy(period, now).map((f, i): Item => ({ id: `f${i}`, kind: 'festival', name: f.name, start: f.start, end: f.end, sido: normalizeSido(f.sido), sigungu: f.sigungu, place: f.place || f.address || '', host: f.host ?? '', lat: f.lat, lng: f.lng, note: '반려동물 축제' }))
    return [...events, ...festivals].sort((a, b) => a.start.localeCompare(b.start))
  }, [period, now])

  const shown = sido ? items.filter(i => i.sido === sido) : items
  const counts = countBySido(items)
  const markers: MapMarker[] = items.filter(i => i.lat !== null && i.lng !== null).map(i => ({
    id: i.id, lat: i.lat!, lng: i.lng!, radius: 10,
    tone: i.kind === 'adoption' ? 'forest' : (i.end || i.start) < now ? 'muted' : 'peach',
    tooltip: `${i.name} · ${dotDate(i.start)}${i.end && i.end !== i.start ? ` ~ ${dotDate(i.end)}` : ''}`,
  }))
  const focusItem = items.find(i => i.id === selected)
  const focus = focusItem?.lat ? { lat: focusItem.lat, lng: focusItem.lng!, zoom: 11 } : sido ? (c => (c ? { lat: c.lat, lng: c.lng, zoom: 8 } : null))(regionCoord(sido, null)) : null

  return <div className="events-explorer">
    <div className="events-toolbar">
      <div className="period-toggle" role="group" aria-label="기간">{PERIODS.map(p => <button key={p.key} type="button" aria-pressed={period === p.key} className={period === p.key ? 'active' : ''} onClick={() => { setPeriod(p.key); setSelected(null) }}>{p.label}</button>)}</div>
      <span className="events-total">입양 행사 <b>{items.filter(i => i.kind === 'adoption').length}</b> · 반려동물 축제 <b>{items.filter(i => i.kind === 'festival').length}</b></span>
    </div>

    <div className="events-layout">
      <div>
        <KoreaMap label="입양 행사·반려동물 축제 지도" markers={markers} selectedId={selected} onSelect={setSelected} focus={focus} height={480} />
        <div className="map-legend">
          <span><i className="tone-forest" />입양 행사</span>
          <span><i className="tone-peach" />반려동물 축제(예정)</span>
          <span><i className="tone-muted" />지난 축제</span>
        </div>
      </div>
      <aside className="sido-counts" aria-label="시·도별 개수">
        <h3>시·도별 행사 수</h3>
        {counts.length > 0
          ? <ul>{counts.map(([s, n]) => <li key={s}><button type="button" className={sido === s ? 'active' : ''} onClick={() => { setSido(sido === s ? null : s); setSelected(null) }}><span>{shortSido(s)}</span><i style={{ width: `${(n / counts[0][1]) * 100}%` }} /><b>{n}</b></button></li>)}</ul>
          : <p className="tier-empty">이 기간에 등록된 행사가 없어요.</p>}
        {sido && <button type="button" className="back-link" onClick={() => setSido(null)}><Icon name="arrow" size={14} />전국 보기</button>}
      </aside>
    </div>

    {adoptionEvents.length === 0 && <div className="support-empty"><Icon name="calendar" size={22} /><div><strong>아직 등록된 입양 행사가 없어요.</strong><p>입양 행사는 보통 시·군·구 소식이나 보호센터 공지로 알려져요. 운영팀이 확인한 행사부터 차례로 올릴게요. 가까운 보호센터에 행사 소식을 물어보세요.</p></div></div>}

    <h3 className="tier-title events-list-title"><span className="tier-dot peach" />{sido ? `${sido} 행사` : '전국 행사'}<small>{shown.length}건</small></h3>
    {shown.length > 0
      ? <div className="event-grid">{shown.map(i => <article key={i.id} className={`event-card${selected === i.id ? ' selected' : ''}${(i.end || i.start) < now ? ' past' : ''}`} onClick={() => setSelected(i.id)}>
        <div className="event-date"><strong>{i.start.slice(5).replace('-', '.')}</strong><span>{i.start === i.end || !i.end ? i.start.slice(0, 4) : `~ ${i.end.slice(5).replace('-', '.')}`}</span></div>
        <div><span className="event-badge">{i.note}{(i.end || i.start) < now ? ' · 지난 행사' : ''}{i.host ? ` · ${i.host}` : ''}</span><h3>{i.name}</h3><p><Icon name="pin" size={13} />{i.sido ? `${shortSido(i.sido)} ${i.sigungu ?? ''} · ` : ''}{i.place}</p></div>
      </article>)}</div>
      : <p className="tier-empty">{period === 'upcoming' ? '앞으로 열리는 행사가 아직 없어요. ‘올해 전체’를 눌러 올해 열린 행사를 볼 수 있어요.' : '이 기간에 등록된 행사가 없어요.'}</p>}

    <p className="reference-note">반려동물 축제는 입양 행사와 다를 수 있어요. 입양 부스가 있는지는 주최 기관에 확인해 주세요. 출처: {support.sources.festivals} · {support.sources.coords}. 문화체육관광부 지역축제정보는 키를 받으면 더할 예정이에요.</p>
  </div>
}
