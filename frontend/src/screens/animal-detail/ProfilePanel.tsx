import { Icon } from '@/components/icons/Icon'
import { animalMeta, availabilityLabel, isAvailable, noticePeriod, noticeProgress, shortDate, statusLabel } from '@/lib/animal'
import type { Animal } from '@/types/animal'

function todayLabel() {
  const d = new Date()
  return `${String(d.getMonth() + 1).padStart(2, '0')}.${String(d.getDate()).padStart(2, '0')}`
}

/** 상세 상단 오른쪽: 공고 요약 · (매칭 이유) · 입양 가능 시점 · (웹) 문의 버튼 */
export function ProfilePanel({ animal: a, compact, onContact, onChecklist }: { animal: Animal; compact: boolean; onContact: () => void; onChecklist: () => void }) {
  const available = isAvailable(a)
  const progress = noticeProgress(a)
  return <div className="profile-panel">
    <div className="notice-meta"><span className={`inline-status ${available ? '' : 'pending'}`}>{statusLabel(a)}</span>{a.noticeNo && <span>{a.noticeNo}</span>}<span>공고 기간 {noticePeriod(a)}</span></div>
    <div className="eyebrow">{a.protectedNow ? '새로운 가족을 기다리고 있어요' : '보호가 끝난 공고예요'}</div><h1>{a.kind} · {a.gender} · {a.ageLabel}{a.age !== null && <span>(추정)</span>}</h1><p className="profile-meta">{animalMeta(a)}</p>
    {a.score !== undefined && <div className="why-box">
      <h2><Icon name="leaf" size={18} />왜 이 아이일까요?<span>매칭 {a.score}%</span></h2>
      {a.reasons?.map(([label, source]) => <div className="why-line" key={label}><span className={`why-dot ${source === '사진' ? 'peach' : source === '조건' ? 'sage' : 'green'}`} /><span>{label}</span><small>{source}</small></div>)}
    </div>}
    {a.protectedNow && <div className={`adoption-timeline ${available ? 'finished' : ''}`}>
      <div className="timeline-heading"><h3>언제 만날 수 있나요?</h3><span>{availabilityLabel(a)}</span></div>
      <div className="timeline-track"><div style={{ width: `${progress}%` }} /><span className="today-marker" style={{ left: `${progress}%` }}>오늘 {todayLabel()}</span><i /><i /><i /></div>
      <div className="timeline-labels"><span><b>{shortDate(a.happenDate)}</b>구조일</span><span><b>{shortDate(a.noticeStart)}</b>공고 시작</span><span><b>{shortDate(a.noticeEnd)}</b>공고 종료<small>이후 입양 가능</small></span></div>
    </div>}
    {!compact && <div className="profile-actions"><button className="peach-button" onClick={onContact}>보호소에 문의하기<Icon name="arrow" size={18} /></button><button className="outline-button" onClick={onChecklist}><Icon name="check" size={16} />방문 전 체크리스트</button></div>}
    <p className="reference-note">입양 가능 여부는 방문 전 보호소에 꼭 확인해 주세요.</p>
  </div>
}
