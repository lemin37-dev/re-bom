import { Icon } from '@/components/icons/Icon'
import { statusLabel } from '@/lib/animal'
import type { Animal } from '@/types/animal'

/** 상세 상단 오른쪽: 공고 요약 · 매칭 이유 · 입양 가능 시점 · (웹) 문의 버튼 */
export function ProfilePanel({ animal: a, noticeNumber, compact, onContact, onChecklist }: { animal: Animal; noticeNumber: string; compact: boolean; onContact: () => void; onChecklist: () => void }) {
  return <div className="profile-panel">
    <div className="notice-meta"><span className={`inline-status ${a.available ? '' : 'pending'}`}>{statusLabel(a)}</span><span>{noticeNumber}</span><span>공고 기간 {a.available ? '10.12 ~ 10.22' : '10.22 ~ 11.01'}</span></div>
    <div className="eyebrow">새로운 가족을 기다리고 있어요</div><h1>{a.kind} · {a.gender} · {a.age}살<span>(추정)</span></h1><p className="profile-meta">{a.kg}kg · {a.color} · {a.care}</p>
    <div className="why-box">
      <h2><Icon name="leaf" size={18} />왜 이 아이일까요?<span>매칭 {a.score}%</span></h2>
      <div className="why-line"><span className="why-dot green" /><span>{a.reasons.find(r => r[1] === '문장')?.[0] || '생활 조건이 잘 맞아요'}</span><small>내 문장</small></div>
      <div className="why-line"><span className="why-dot peach" /><span>첨부한 사진과 외형이 닮았어요</span><small>내 사진</small></div>
      <div className="why-line"><span className="why-dot sage" /><span>{a.kg < 8 ? '소형' : '중형'} · {a.age}살, 선택한 조건에 가까워요</span><small>내 조건</small></div>
      <p>문장·사진·조건 매칭을 설명하기 위한 가상 예시예요.</p>
    </div>
    <div className={`adoption-timeline ${a.available ? 'finished' : ''}`}>
      <div className="timeline-heading"><h3>언제 만날 수 있나요?</h3><span>{a.available ? '지금 입양 상담 가능' : '11월 1일 공고 종료 이후 입양 가능'}</span></div>
      <div className="timeline-track"><div /><span className="today-marker">오늘 10.29</span><i /><i /><i /></div>
      <div className="timeline-labels"><span><b>{a.available ? '10.11' : '10.21'}</b>구조일</span><span><b>{a.available ? '10.12' : '10.22'}</b>공고 시작</span><span><b>{a.available ? '10.22' : '11.01'}</b>공고 종료<small>이후 입양 가능</small></span></div>
    </div>
    {!compact && <div className="profile-actions"><button className="peach-button" onClick={onContact}>보호소에 문의하기<Icon name="arrow" size={18} /></button><button className="outline-button" onClick={onChecklist}><Icon name="check" size={16} />방문 전 체크리스트</button></div>}
    <p className="reference-note">입양 가능 여부는 방문 전 보호소에 꼭 확인해 주세요.</p>
  </div>
}
