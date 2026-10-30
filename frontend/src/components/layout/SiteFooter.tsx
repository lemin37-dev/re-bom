'use client'

import { Icon } from '@/components/icons/Icon'
import { MascotArt } from '@/components/mascot/MascotArt'

export function SiteFooter({ onMascot }: { onMascot: () => void }) {
  return <footer className="site-footer"><div className="footer-inner">
    <div className="footer-top"><span className="footer-logo">RE:<span>봄</span></span><span>한 번의 만남이, 평생의 봄으로.</span></div>
    <div className="footer-bottom">
      <div>
        <p>데이터 출처: 농림축산식품부 국가동물보호정보시스템(공공데이터포털)</p>
        <p>공고 데이터는 매일 오전 03:00에 갱신됩니다.</p>
        <p>현재는 실시간 연동 없이 가상 데이터로 동작하는 예시 화면입니다.</p>
        <p>공고 정보는 보호소 사정에 따라 바뀔 수 있어요. 방문 전 보호소에 꼭 확인하세요.</p>
      </div>
      <span>예시 화면 · 가상 데이터</span>
    </div>
    <button className="mascot-footer-button" aria-haspopup="dialog" onClick={onMascot}><MascotArt mascot="remeong" /><MascotArt mascot="bomnyang" />리멍이와 봄냥이 이야기<Icon name="arrow" size={14} /></button>
  </div></footer>
}
