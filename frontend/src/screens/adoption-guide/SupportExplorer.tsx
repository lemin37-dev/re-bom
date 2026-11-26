'use client'

import { type FormEvent, useEffect, useMemo, useState } from 'react'
import { KoreaMap, type MapMarker } from '@/components/map/KoreaMap'
import { Icon } from '@/components/icons/Icon'
import { SIDOS, benefitCountBySido, benefitPlacesIn, benefitsFor, nearestBenefits, normalizeSido, parseRegionId, programKindLabel, regionCoord, regionId, searchRegions, shortSido, support } from '@/lib/adoption-support'
import { regionOfCoord } from '@/lib/kakao'
import { RegionSupportPanel } from '../common/RegionSupportPanel'

export type RegionPick = { sido: string; sigungu: string | null } | null

/**
 * 우리 지역 입양 혜택 (/guide/support): 확인된 혜택만 지도·목록에 보여 줍니다.
 * 확인되지 않은 지역은 검색하면 안내 문구가 나옵니다.
 */
export function SupportExplorer() {
  const [pick, setPick] = useState<RegionPick>(null)
  const [query, setQuery] = useState('')
  const [choices, setChoices] = useState<{ sido: string; sigungu: string | null }[]>([])
  const [notFound, setNotFound] = useState<string | null>(null)
  const [me, setMe] = useState<{ lat: number; lng: number } | null>(null)
  const [locating, setLocating] = useState<'idle' | 'busy' | 'denied' | 'near'>('idle')

  // 공고 상세 등에서 ?sido=&sigungu= 로 들어오면 그 지역을 바로 보여 줌
  useEffect(() => {
    const q = new URLSearchParams(window.location.search)
    const sido = normalizeSido(q.get('sido'))
    // eslint-disable-next-line react-hooks/set-state-in-effect -- 주소의 지역을 처음 한 번만 반영
    if (sido) setPick({ sido, sigungu: q.get('sigungu') || null })
  }, [])

  const bySido = useMemo(() => benefitCountBySido(), [])
  const sidosWithBenefit = SIDOS.filter(t => bySido.has(t.sido))

  const markers: MapMarker[] = useMemo(() => {
    if (!pick) return sidosWithBenefit.flatMap(t => {
      const c = regionCoord(t.sido, null)
      const v = bySido.get(t.sido)!
      return c ? [{ id: regionId(t.sido, null), lat: c.lat, lng: c.lng, pill: true, radius: 12, tone: 'forest', label: `${t.short} ${v.total}`, tooltip: `${t.sido} · 확인된 입양 혜택 ${v.total}가지` } satisfies MapMarker] : []
    })
    const out: MapMarker[] = []
    const sidoWide = benefitsFor(pick.sido, null).sidoWide
    const sc = regionCoord(pick.sido, null)
    if (sidoWide.length && sc) out.push({ id: regionId(pick.sido, null), lat: sc.lat, lng: sc.lng, pill: true, radius: 12, tone: 'forest', label: `${shortSido(pick.sido)} 전체 ${sidoWide.length}`, tooltip: `${pick.sido} 전체에 해당하는 혜택 ${sidoWide.length}가지` })
    for (const name of benefitPlacesIn(pick.sido)) {
      const c = regionCoord(pick.sido, name)
      const local = benefitsFor(pick.sido, name).local
      if (c) out.push({ id: regionId(pick.sido, name), lat: c.lat, lng: c.lng, radius: 11, tone: local.some(p => p.kinds.includes('cost')) ? 'forest' : 'peach', label: String(local.length), tooltip: `${name} · ${local.map(p => p.benefit.headline).join(' · ')}` })
    }
    return out
  }, [pick, bySido, sidosWithBenefit])

  const focus = useMemo(() => {
    if (!pick) return me && locating === 'near' ? { lat: me.lat, lng: me.lng, zoom: 8.5 } : null
    const c = regionCoord(pick.sido, pick.sigungu) ?? regionCoord(pick.sido, null)
    // 특별시·광역시는 구가 촘촘해 더 가까이
    const metro = /특별시|광역시/.test(pick.sido)
    return c ? { lat: c.lat, lng: c.lng, zoom: pick.sigungu ? 10.5 : metro ? 10.5 : 8.5 } : null
  }, [pick, me, locating])

  function choose(p: RegionPick) {
    setChoices([])
    setNotFound(null)
    setPick(p)
  }

  /** 내 위치 → 카카오로 시·군·구를 찾으면 그 지역, 못 찾으면 가까운 혜택 지역 */
  function locate() {
    if (!navigator.geolocation) { setLocating('denied'); return }
    setLocating('busy')
    navigator.geolocation.getCurrentPosition(async pos => {
      const here = { lat: pos.coords.latitude, lng: pos.coords.longitude }
      setMe(here)
      const region = await regionOfCoord(here.lat, here.lng)
      const sido = normalizeSido(region?.sido)
      if (sido) { setLocating('idle'); choose({ sido, sigungu: region?.sigungu ?? null }); return }
      choose(null)
      setLocating('near')
    }, () => setLocating('denied'), { enableHighAccuracy: false, timeout: 10000, maximumAge: 600000 })
  }

  function submit(e: FormEvent) {
    e.preventDefault()
    const found = searchRegions(query)
    if (found.length === 1) choose(found[0])
    else if (found.length > 1) { setChoices(found); setNotFound(null) }
    else { setChoices([]); setNotFound(query.trim() || null); setPick(null) }
  }

  return <div className="support-explorer">
    <div className="support-map-col">
      <form className="support-search" onSubmit={submit} role="search">
        <Icon name="search" size={18} />
        <input value={query} onChange={e => setQuery(e.target.value)} placeholder="입양할 지역 검색 (예: 관악구, 경남, 정읍시)" aria-label="지역 이름" />
        <button type="submit">찾기</button>
      </form>
      <button type="button" className="locate-button" onClick={locate} disabled={locating === 'busy'}><Icon name="pin" size={16} />{locating === 'busy' ? '위치를 찾는 중…' : '내 위치로 찾기'}</button>
      {locating === 'denied' && <p className="locate-status">위치를 가져오지 못했어요. 브라우저의 위치 권한을 확인하거나 지역을 검색해 주세요.</p>}
      {choices.length > 0 && <div className="region-choices"><span>어느 지역인가요?</span>{choices.map(c => <button key={c.sido + c.sigungu} type="button" onClick={() => choose(c)}>{c.sido} {c.sigungu}</button>)}</div>}
      <KoreaMap label="입양 혜택이 확인된 지역 지도" me={me} markers={markers} selectedId={pick ? regionId(pick.sido, pick.sigungu) : null} onSelect={id => choose(parseRegionId(id))} focus={focus} />
      <p className="support-map-note">확인된 혜택이 있는 지역만 지도에 보여요. 다른 지역은 검색해 보세요.</p>
      {sidosWithBenefit.length > 0 && <div className="sido-chips"><button type="button" className={!pick ? 'active' : ''} onClick={() => choose(null)}>전국</button>{sidosWithBenefit.map(t => <button key={t.sido} type="button" className={pick?.sido === t.sido ? 'active' : ''} onClick={() => choose({ sido: t.sido, sigungu: null })}>{t.short}<small>{bySido.get(t.sido)!.total}</small></button>)}</div>}
    </div>

    <div className="support-side">
      {notFound && <div className="support-empty"><Icon name="pin" size={22} /><div><strong>‘{notFound}’에는 아직 확인된 입양 혜택이 없어요.</strong><p>혜택이 없다는 뜻은 아니에요. 운영팀이 지역 혜택을 계속 확인하고 있어요. 입양할 보호센터나 시·군·구 동물보호 담당 부서에 입양비·펫보험 지원이 있는지 물어보세요.</p></div></div>}

      {!notFound && !pick && locating === 'near' && me && <NearView me={me} onPick={choose} />}
      {!notFound && !pick && <Overview onPick={choose} />}
      {!notFound && pick && !pick.sigungu && <SidoView sido={pick.sido} onPick={choose} />}
      {!notFound && pick?.sigungu && <>
        <button type="button" className="back-link" onClick={() => choose({ sido: pick.sido, sigungu: null })}><Icon name="arrow" size={14} />{pick.sido} 보기</button>
        <h3 className="region-title">{pick.sido} {pick.sigungu}</h3>
        <RegionSupportPanel sido={pick.sido} sigungu={pick.sigungu} />
      </>}
    </div>
  </div>
}

/** 처음 화면: 지금 확인된 혜택을 지역별로 한 줄씩 */
function Overview({ onPick }: { onPick: (p: RegionPick) => void }) {
  const cost = support.programs.filter(p => p.kinds.includes('cost')).length
  return <div className="support-overview">
    <h3>지금 확인된 입양 혜택 <b>{support.programs.length}</b>가지</h3>
    <p className="overview-lead">입양비 지원 {cost}가지 · 펫보험 지원 {support.programs.length - cost}가지. 지역을 누르면 받을 수 있는 조건과 신청 방법을 볼 수 있어요.</p>
    <ul className="benefit-index">{support.programs.map(p => <li key={p.id}><button type="button" onClick={() => onPick({ sido: normalizeSido(p.sido)!, sigungu: p.sigungu })}>
      <span className={`kind-chip ${p.kinds.includes('cost') ? 'kind-cost' : 'kind-insurance'}`}>{programKindLabel(p)}</span>
      <strong>{p.sigungu ? `${shortSido(normalizeSido(p.sido)!)} ${p.sigungu}` : `${p.sido} 전체`}</strong>
      <span>{p.benefit.headline}</span>
      <Icon name="arrow" size={14} />
    </button></li>)}</ul>
    <p className="overview-note">정부 혜택 정보(공공서비스)에 올라온 사업만 보여 드려요. 운영팀이 지역 혜택을 계속 확인해 늘려 갈게요.</p>
  </div>
}

function SidoView({ sido, onPick }: { sido: string; onPick: (p: RegionPick) => void }) {
  const places = benefitPlacesIn(sido)
  const sidoWide = benefitsFor(sido, null).sidoWide
  return <>
    <button type="button" className="back-link" onClick={() => onPick(null)}><Icon name="arrow" size={14} />전국 보기</button>
    <h3 className="region-title">{sido}</h3>
    {sidoWide.length > 0 && <><h4 className="sido-subtitle">{sido} 어디서 입양해도 받을 수 있는 혜택</h4><RegionSupportPanel sido={sido} sigungu={null} /></>}
    {places.length > 0 && <>
      <h4 className="sido-subtitle">시·군·구 혜택</h4>
      <ul className="place-list">{places.map(name => <li key={name}><button type="button" onClick={() => onPick({ sido, sigungu: name })}>
        <strong>{name}</strong>
        <span className="place-benefits">{benefitsFor(sido, name).local.map(p => p.benefit.headline).join(' · ')}</span>
        <Icon name="arrow" size={14} />
      </button></li>)}</ul>
    </>}
    {sidoWide.length === 0 && places.length === 0 && <div className="support-empty"><Icon name="pin" size={22} /><div><strong>{sido}에는 아직 확인된 입양 혜택이 없어요.</strong><p>입양할 보호센터나 시·군·구 동물보호 담당 부서에 입양비·펫보험 지원이 있는지 물어보세요.</p></div></div>}
    {places.length > 0 && <p className="reference-note">목록에 없는 시·군·구는 아직 확인된 혜택이 없어요.</p>}
  </>
}

/** 내 위치에서 가까운 확인된 혜택 (시·군·구를 정확히 못 찾았을 때) */
function NearView({ me, onPick }: { me: { lat: number; lng: number }; onPick: (p: RegionPick) => void }) {
  const near = nearestBenefits(me)
  return <div className="support-overview near-view">
    <h3>내 위치에서 가까운 입양 혜택</h3>
    <p className="overview-lead">가까운 순서예요. 지원은 입양한 보호센터의 지역 기준이라, 가까운 지역 보호센터에서 입양하면 받을 수 있어요. 위치는 이 화면에서만 쓰고 저장하지 않아요.</p>
    <ul className="benefit-index near-list">{near.map(({ program: p, km }) => <li key={p.id}><button type="button" onClick={() => onPick({ sido: normalizeSido(p.sido)!, sigungu: p.sigungu })}>
      <span className="kind-chip kind-cost">약 {km}km</span>
      <strong>{p.sigungu ? `${shortSido(normalizeSido(p.sido)!)} ${p.sigungu}` : `${p.sido} 전체`}</strong>
      <span>{p.benefit.headline}</span>
      <Icon name="arrow" size={14} />
    </button></li>)}</ul>
  </div>
}
