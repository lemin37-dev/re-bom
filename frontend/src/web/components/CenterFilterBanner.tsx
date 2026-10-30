'use client'

import { Icon } from '@/components/icons/Icon'

export function CenterFilterBanner({ center, onClear }: { center: string; onClear: () => void }) {
  return <div className="center-filter-banner"><span><Icon name="pin" size={17} /><strong>{center}</strong>에서 기다리는 친구들이에요.</span><button onClick={onClear}>센터 조건 해제<Icon name="close" size={14} /></button></div>
}
