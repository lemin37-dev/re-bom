import Link from 'next/link'
import { MascotState } from '@/components/mascot/MascotState'
import { ROUTES } from '@/constants/routes'

export default function NotFound() {
  return <section className="expanded-detail">
    <MascotState kind="not-found"><Link className="primary-button" href={ROUTES.search}>입양 찾기로 돌아가기</Link></MascotState>
  </section>
}
