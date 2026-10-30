# RE:봄 frontend

유기동물 입양 매칭 서비스의 프론트엔드입니다. Next.js(App Router) + React + Tailwind CSS v4 + TypeScript.

## 실행

```bash
pnpm install
pnpm dev        # 개발 서버 (기본 http://localhost:3000, PORT 환경 변수로 변경)
pnpm build      # 프로덕션 빌드 (모든 화면 정적 생성)
pnpm start      # 빌드 결과 실행
pnpm lint       # ESLint (eslint-config-next)
pnpm typecheck  # tsc --noEmit
```

## 기술 스택

- Runtime: Next.js 16 (App Router), React 19, React DOM 19
- Styling: Tailwind CSS v4 (`@tailwindcss/postcss`, `postcss.config.mjs`) + 기존 CSS 파일
- Language: TypeScript 5.9 (strict), 경로 별칭 `@/*` → `src/*`
- Lint/Format: ESLint 9 + eslint-config-next, oxfmt
- Node.js 20.9 이상 (`.mise.toml`)

## 폴더 구조

```
src/
├─ app/                  # Next.js 라우트 (얇게 유지: 화면 컴포넌트만 렌더)
│  ├─ layout.tsx         # <html>, 전역 CSS, Providers, ExperienceShell
│  ├─ page.tsx           # /             입양 찾기
│  ├─ animals/[id]/      # /animals/:id  동물 상세 (generateStaticParams로 정적 생성)
│  ├─ guide/             # /guide        입양 절차 안내
│  ├─ shelters/          # /shelters     보호센터
│  ├─ about/             # /about        서비스 소개
│  └─ not-found.tsx
│
├─ web/                  # 웹(넓은 화면) 전용 화면 구성·배치
│  ├─ layout/            # WebHeader
│  ├─ screens/           # WebSearchScreen (입양 찾기 화면 배치)
│  ├─ components/        # SearchCard, SearchSuggestions, SearchResults, ResultMap, CenterFilterBanner
│  └─ styles/            # header.css, search.css
│
├─ mobile/               # 앱(모바일 프레임) 전용 화면 구성·배치
│  ├─ layout/            # MobileHeader, MobileTabs, AppPreviewAside
│  ├─ screens/           # MobileSearchScreen
│  ├─ components/        # MobileHero, MobileSearchForm, MobileSuggestions, MobileResults, MobilePetCard
│  └─ styles/            # mobile.css (.app-preview-mode / .mobile-app 범위)
│
│  ── 이하 웹·앱 공통 ──
├─ screens/              # 웹·앱이 같은 본문을 쓰는 화면 (앱에서는 compact 속성으로 조정)
│  ├─ search/            # SearchScreen: 모드에 따라 web/mobile 화면 선택
│  ├─ animal-detail/     # AnimalDetail + PetGallery, ProfilePanel, DetailInformation, DetailModal
│  ├─ adoption-guide/    # AdoptionGuide
│  ├─ shelter-directory/ # ShelterDirectory
│  ├─ about/             # AboutPage
│  └─ common/            # Checklist, SupportCards, RegionSelect (여러 화면에서 사용)
├─ components/           # 공통 UI
│  ├─ layout/            # ExperienceShell(웹/앱 틀 조립), PreviewControls, SiteFooter
│  ├─ animal/            # AnimalArt, PetCard, Reasons
│  ├─ mascot/            # MascotArt, HeroMascot, ThemeToggle, MascotState, MascotDialog
│  ├─ search/            # FilterDialog
│  ├─ map/               # IllustratedMap
│  ├─ icons/             # Icon
│  └─ ui/                # SectionTitle
├─ providers/            # 전역 상태 (Context)
│  ├─ ViewModeProvider   # 웹/앱 모드
│  ├─ SearchProvider     # 검색어·사진·조건·정렬 등 검색 상태 (모드 전환 시 유지)
│  ├─ MascotProvider     # 마스코트 테마
│  └─ ExperienceProvider # 화면 이동, 스크롤 복원, 모드 전환, 상세 하단 버튼 포털 자리
├─ hooks/                # useScrollMemory
├─ data/                 # 가상 데이터·상수 (animals, shelters, filters, adoption-guide, mascots)
├─ lib/                  # 순수 로직 (search: 필터·정렬, animal: 공고번호·태그·비슷한 아이들)
├─ constants/            # routes (경로, 주 메뉴)
├─ types/                # Animal, Shelter, Filters, ViewMode
└─ styles/               # globals.css(진입점), tokens, fonts, 공통 CSS
```

### 어디에 코드를 둘까

- 웹/앱 중 한쪽에서만 쓰는 화면 배치·컴포넌트 → `src/web/` 또는 `src/mobile/`
- 양쪽이 공유하는 화면 본문 → `src/screens/`, 작은 UI → `src/components/`
- 새 페이지 → `src/app/<경로>/page.tsx`는 화면 컴포넌트만 렌더하고, 로직은 `screens/`에 둡니다.
- 데이터 모양은 `src/types/`, 실제 API 연동 시 `src/data/`의 가상 데이터를 대체합니다.

## 스타일

- `src/styles/globals.css`가 유일한 CSS 진입점이며 모든 CSS 파일을 `@import`합니다.
  **import 순서가 cascade 순서**이므로 순서를 바꾸지 마세요.
- 디자인 토큰은 `src/styles/tokens.css`의 Tailwind v4 `@theme`에 있습니다 (`--color-forest` 등).
- Tailwind 유틸리티 클래스도 사용할 수 있습니다. 전역 요소 스타일은 `@layer base` 안에 둡니다.
- 한글 글꼴은 `src/styles/fonts.css`의 `@font-face`가 `public/fonts/`의 Noto Sans KR WOFF2를 유니코드 범위별로 불러옵니다.

## 동작 메모

- 상단 토글로 웹/앱 미리보기를 전환하며, 검색 상태와 보고 있는 화면은 그대로 유지됩니다.
- 화면 이동은 `useExperience().navigate(href)`를 사용하세요. 모드·경로별 스크롤 위치 복원을 함께 처리합니다.
- 모든 동물·보호센터 정보는 가상 데이터이며 서버·AI 분석은 연결되어 있지 않습니다.
