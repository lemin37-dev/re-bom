import Link from 'next/link'
import { ROUTES } from '@/constants/routes'

export default function NotFound() {
  return <section className="page-heading">
    <div>
      <h1>찾으시는 화면이 없어요</h1>
      <p>주소가 바뀌었거나, 이미 새 가족을 만난 친구일 수 있어요.</p>
      <p><Link className="primary-button" href={ROUTES.search}>입양 찾기로 돌아가기</Link></p>
    </div>
  </section>
}
