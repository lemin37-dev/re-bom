'use client'

import { useEffect, useRef, useState } from 'react'
import { Icon } from '@/components/icons/Icon'
import { mascotProfiles, mascotThemes, poseNames, type MascotTheme } from '@/data/mascots'
import { useMascot } from '@/providers/MascotProvider'
import { MascotArt } from './MascotArt'

export function MascotDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { theme } = useMascot()
  const [profile, setProfile] = useState<MascotTheme>(theme)
  const [pose, setPose] = useState('portrait')
  const dialog = useRef<HTMLDialogElement>(null)
  // 열 때마다 현재 테마의 마스코트 초상화부터 보여 줍니다.
  const [wasOpen, setWasOpen] = useState(open)
  if (open !== wasOpen) {
    setWasOpen(open)
    if (open) { setProfile(theme); setPose('portrait') }
  }
  useEffect(() => { if (open) dialog.current?.showModal(); else dialog.current?.close() }, [open])
  const info = mascotProfiles[profile]

  function selectProfile(next: MascotTheme) {
    setProfile(next)
    setPose('portrait')
  }

  return (
    <dialog ref={dialog} className="mascot-dialog" aria-labelledby="mascot-title" onCancel={onClose} onClick={e => { if (e.target === dialog.current) onClose() }}>
      <div className="dialog-header">
        <div>
          <span className="eyebrow">다시, 봄이 올 수 있도록</span>
          <h2 id="mascot-title">우리의 봄을 함께하는 친구들</h2>
        </div>
        <button autoFocus className="icon-button" onClick={onClose} aria-label="마스코트 소개 닫기"><Icon name="close" /></button>
      </div>
      <p className="mascot-dialog-intro">기다림에도, 새로운 만남에도 함께할 RE:봄의 마스코트예요.</p>
      <div
        className="mascot-profile-tabs"
        role="tablist"
        aria-label="마스코트 소개"
        onKeyDown={e => {
          if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(e.key)) return
          e.preventDefault()
          const next: MascotTheme = e.key === 'Home' ? 'remeong' : e.key === 'End' ? 'bomnyang' : profile === 'remeong' ? 'bomnyang' : 'remeong'
          selectProfile(next)
          document.getElementById(`mascot-tab-${next}`)?.focus()
        }}
      >
        {mascotThemes.map(key => (
          <button role="tab" id={`mascot-tab-${key}`} aria-controls="mascot-profile" tabIndex={profile === key ? 0 : -1} aria-selected={profile === key} key={key} onClick={() => selectProfile(key)}>
            <MascotArt mascot={key} />
            <span><strong>{mascotProfiles[key].name}</strong><small>{mascotProfiles[key].animal}</small></span>
          </button>
        ))}
      </div>
      <section id="mascot-profile" role="tabpanel" aria-labelledby={`mascot-tab-${profile}`}>
        <div className="mascot-profile-visual">
          <MascotArt mascot={profile} pose={pose} decorative={false} />
          <span>“{info.tagline}”</span>
        </div>
        <div className="mascot-profile-copy">
          <h3>안녕, 나는 {info.name}!</h3>
          <p>{info.description}</p>
          <dl>
            <div><dt>성격</dt><dd>{info.personality}</dd></div>
            <div><dt>노란 리본</dt><dd>다시 만남의 약속. 잃어버린 인연과 새로운 가족을 이어주는 마음을 담았어요.</dd></div>
            <div><dt>모습의 의미</dt><dd>{info.interpretation}</dd></div>
          </dl>
        </div>
        <div className="mascot-pose-heading">
          <strong>이런 순간에도 함께해요</strong>
          <span>표정을 눌러 만나 보세요</span>
        </div>
        <div className="mascot-pose-grid">
          {info.poses.map(item => (
            <button key={item} aria-pressed={pose === item} onClick={() => setPose(item)}>
              <MascotArt mascot={profile} pose={item} />
              <span>{poseNames[item]}</span>
            </button>
          ))}
        </div>
      </section>
      <p className="mascot-theme-note"><Icon name="leaf" size={15} />상단 토글은 화면의 분위기만 바꿔요. 찾던 친구와 검색 조건은 그대로예요.</p>
    </dialog>
  )
}
