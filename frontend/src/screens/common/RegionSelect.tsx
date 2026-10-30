'use client'

import { regions } from '@/data/shelters'

export function RegionSelect({ region, district, onChange }: { region: string; district: string; onChange: (region: string, district: string) => void }) {
  return <div className="region-select">
    <label>시·도<select value={region} onChange={e => onChange(e.target.value, regions[e.target.value][0])}>{Object.keys(regions).map(r => <option key={r}>{r}</option>)}</select></label>
    <label>시·군·구<select value={district} onChange={e => onChange(region, e.target.value)}>{regions[region].map(d => <option key={d}>{d}</option>)}</select></label>
  </div>
}
