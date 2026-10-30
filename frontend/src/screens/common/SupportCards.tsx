import { Icon } from '@/components/icons/Icon'

/** 지역별 입양비·펫보험 지원 예시 카드 */
export function SupportCards({ region, district }: { region: string; district: string }) {
  const amount = region === '경기도' ? 25 : region === '서울특별시' ? 20 : 15
  const insurance = region === '인천광역시' ? '예) 펫보험 가입비 일부 지원' : '예) 입양 후 1년 펫보험 무료'
  return <>
    <div className="support-grid">
      <article className="support-card"><span className="support-type"><Icon name="leaf" size={17} />입양비 지원</span><h3>예) 마리당 최대 <strong>{amount}만원</strong></h3><p>{region} {district} · 입양 후 1년 안에 신청</p><ul><li>진료·예방접종·중성화·동물등록비 등</li><li>지원 신청 시 내장형 동물등록 필요</li></ul><small>지자체마다 금액·조건이 달라요.<br />예산이 끝나면 일찍 마감될 수 있어요.</small></article>
      <article className="support-card insurance-card"><span className="support-type"><Icon name="check" size={17} />펫보험 지원</span><h3>{insurance}</h3><p>{region} {district} · 보장 내용은 지자체 확인</p><ul><li>질병·상해 치료비 등의 보장 여부 확인</li><li>가입 대상·자기부담금·제외 항목 확인 필요</li></ul><small>지자체마다 금액·조건이 달라요.<br />예산이 끝나면 일찍 마감될 수 있어요.</small></article>
    </div>
    <p className="reference-note">지원 금액과 조건은 화면 시연을 위한 예시이며, 실제 정책은 해당 지자체에 확인해 주세요.</p>
  </>
}
