'use client'

import { mascotProfiles, mascotThemes } from '@/data/mascots'
import { useMascot } from '@/providers/MascotProvider'
import { MascotArt } from './MascotArt'

export function ThemeToggle() {
  const { theme, setTheme } = useMascot()
  return (
    <div className="mascot-theme-toggle" role="group" aria-label="마스코트 화면 테마 · 검색 조건은 유지됩니다">
      {mascotThemes.map(key => (
        <button key={key} className={theme === key ? 'selected' : ''} aria-pressed={theme === key} onClick={() => setTheme(key)} title={`${mascotProfiles[key].name} 화면 테마 · 검색 조건은 바뀌지 않아요`}>
          <MascotArt mascot={key} />
          <span>{mascotProfiles[key].name}</span>
        </button>
      ))}
    </div>
  )
}
