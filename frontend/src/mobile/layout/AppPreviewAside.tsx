import { Icon } from '@/components/icons/Icon'
import { NAV_ITEMS } from '@/constants/routes'

/** 앱 모드에서 모바일 프레임 옆에 보이는 안내 패널 (넓은 화면 전용) */
export function AppPreviewAside({ activeHref, detail, onNavigate }: { activeHref: string; detail: boolean; onNavigate: (href: string) => void }) {
  return <aside className="app-preview-aside">
    <div className="eyebrow"><span className="live-dot" />앱 최적화 미리보기</div>
    <h1>어디에서 만나도,<br />같은 <span>봄.</span></h1>
    <p>익숙한 이야기와 따뜻한 색감은 그대로.<br />손 안에서는 조금 더 간결하고 편안하게.</p>
    <div className="preview-page-list"><span>화면 둘러보기</span>{NAV_ITEMS.map(item => <button key={item.href} onClick={() => onNavigate(item.href)} className={activeHref === item.href ? 'current' : ''}><Icon name={item.icon} size={18} />{item.title}<span>{activeHref === item.href ? detail ? '상세 보는 중' : '보는 중' : '이동'}</span></button>)}</div>
    <span className="native-disclaimer">모바일 앱 사용성을 확인하는 웹 프로토타입입니다.</span>
  </aside>
}
