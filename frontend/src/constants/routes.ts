import type { IconName } from '@/components/icons/Icon'

export const ROUTES = {
  search: '/',
  guide: '/guide',
  shelters: '/shelters',
  about: '/about',
  animal: (id: number) => `/animals/${id}`,
} as const

/** 주 메뉴: 웹 헤더, 앱 하단 탭, 앱 미리보기 안내에서 함께 사용 */
export const NAV_ITEMS: { href: string; title: string; tabLabel: string; icon: IconName }[] = [
  { href: ROUTES.search, title: '입양 찾기', tabLabel: '입양 찾기', icon: 'search' },
  { href: ROUTES.guide, title: '입양 절차 안내', tabLabel: '입양 안내', icon: 'calendar' },
  { href: ROUTES.shelters, title: '보호센터', tabLabel: '보호센터', icon: 'pin' },
  { href: ROUTES.about, title: '서비스 소개', tabLabel: '서비스 소개', icon: 'leaf' },
]

export function isAnimalDetail(pathname: string) {
  return pathname.startsWith('/animals/')
}

/** 현재 경로가 속한 주 메뉴 (동물 상세는 '입양 찾기'에 속함) */
export function activeNavHref(pathname: string) {
  if (isAnimalDetail(pathname)) return ROUTES.search
  return NAV_ITEMS.find(item => item.href === pathname)?.href ?? ROUTES.search
}

export function navTitle(href: string) {
  return NAV_ITEMS.find(item => item.href === href)?.title ?? ''
}
