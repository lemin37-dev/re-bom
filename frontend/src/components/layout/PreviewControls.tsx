import { Icon } from '@/components/icons/Icon'
import type { ViewMode } from '@/types/view-mode'

function DeviceIcon({ phone }: { phone?: boolean }) {
  return <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" aria-hidden="true">{phone ? <><rect x="6" y="2" width="12" height="20" rx="3" /><path d="M10 18h4" /></> : <><rect x="2" y="3" width="20" height="14" rx="2" /><path d="M12 17v4m-5 0h10" /></>}</svg>
}

/** 최상단 웹/앱 화면 전환 바 */
export function PreviewControls({ mode, onChange }: { mode: ViewMode; onChange: (mode: ViewMode) => void }) {
  return <div className="preview-controls">
    <span className="preview-brand"><Icon name="leaf" size={16} />RE:봄 <span>서비스 미리보기</span></span>
    <div className="mode-switch" role="group" aria-label="앱과 웹 화면 전환">
      <button className={mode === 'web' ? 'selected' : ''} aria-pressed={mode === 'web'} onClick={() => onChange('web')}><DeviceIcon />웹 화면</button>
      <button className={mode === 'app' ? 'selected' : ''} aria-pressed={mode === 'app'} onClick={() => onChange('app')}><DeviceIcon phone />앱 화면</button>
    </div>
    <span className="preview-hint">{mode === 'app' ? '작은 화면에 맞춘, 같은 서비스' : '넓은 화면에서 여유롭게 살펴보세요'}</span>
  </div>
}
