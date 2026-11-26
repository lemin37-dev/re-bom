# RE:봄 frontend

유기동물 입양 매칭 서비스의 프론트엔드입니다. Next.js(App Router) + React + Tailwind CSS v4 + TypeScript.

## 실행

```bash
pnpm install
pnpm dev        # 개발 서버 (기본 http://localhost:3000, PORT 환경 변수로 변경)
pnpm build      # 프로덕션 빌드 (동물 상세는 요청 시 렌더)
pnpm dev:lan   # 같은 Wi-Fi의 휴대폰에서 접속할 때 (0.0.0.0 바인딩)
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
│  ├─ animals/[id]/      # /animals/:구조번호  동물 상세 (브라우저에서 API로 불러옴)
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
│  ├─ layout/            # MobileHeader, MobileTabs
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
│  ├─ layout/            # ExperienceShell(웹/앱 틀 조립), SiteFooter
│  ├─ animal/            # AnimalArt, PetCard, Reasons
│  ├─ mascot/            # MascotArt, HeroMascot, ThemeToggle, MascotState, MascotDialog
│  ├─ search/            # FilterDialog, TagPicker(사진 태그로 찾기)
│  ├─ map/               # IllustratedMap
│  ├─ icons/             # Icon
│  └─ ui/                # SectionTitle
├─ providers/            # 전역 상태 (Context)
│  ├─ ViewModeProvider   # 웹/앱 모드 (화면 폭 자동, NEXT_PUBLIC_VIEW_MODE 로 고정 가능)
│  ├─ SearchProvider     # 검색어·사진·조건·정렬 등 검색 상태 (모드 전환 시 유지)
│  ├─ MascotProvider     # 마스코트 테마
│  └─ ExperienceProvider # 화면 이동, 스크롤 복원, 상세 하단 버튼 포털 자리
├─ hooks/                # useScrollMemory, useTagCatalog(사진 태그 사전 한 번만 받기)
├─ data/                 # 상수·예시 데이터 (filters, shelters(지역 예시), adoption-guide, mascots)
├─ lib/                  # api: 검색 API 호출·응답 변환, search: 조건→API 파라미터, animal: 상태·날짜·태그
├─ constants/            # routes (경로, 주 메뉴)
├─ types/                # Animal, Shelter, Filters, ViewMode, TagCatalog
└─ styles/               # globals.css(진입점), tokens, fonts, 공통 CSS
```

### 어디에 코드를 둘까

- 웹/앱 중 한쪽에서만 쓰는 화면 배치·컴포넌트 → `src/web/` 또는 `src/mobile/`
- 양쪽이 공유하는 화면 본문 → `src/screens/`, 작은 UI → `src/components/`
- 새 페이지 → `src/app/<경로>/page.tsx`는 화면 컴포넌트만 렌더하고, 로직은 `screens/`에 둡니다.
- 데이터 모양은 `src/types/`. 서버 데이터는 `src/lib/api.ts`로만 불러오고, 응답 변환(`toAnimal`)도 그곳에 둡니다.

## 스타일

- `src/styles/globals.css`가 유일한 CSS 진입점이며 모든 CSS 파일을 `@import`합니다.
  **import 순서가 cascade 순서**이므로 순서를 바꾸지 마세요.
- 디자인 토큰은 `src/styles/tokens.css`의 Tailwind v4 `@theme`에 있습니다 (`--color-forest` 등).
- Tailwind 유틸리티 클래스도 사용할 수 있습니다. 전역 요소 스타일은 `@layer base` 안에 둡니다.
- 한글 글꼴은 `src/styles/fonts.css`의 `@font-face`가 `public/fonts/`의 Noto Sans KR WOFF2를 유니코드 범위별로 불러옵니다.

## 동작 메모

- 웹/앱 화면은 화면 폭으로 자동 선택합니다(`ViewModeProvider`, 768px 이상 웹). 하이브리드 앱(웹뷰) 빌드처럼 항상 앱 화면이 필요하면 `.env.local` 에 `NEXT_PUBLIC_VIEW_MODE=app` (웹 고정은 `web`).
- 앱 화면은 미리보기 틀 없이 화면 전체(`100dvh`)를 씁니다.
- 화면 이동은 `useExperience().navigate(href)`를 사용하세요. 모드·경로별 스크롤 위치 복원을 함께 처리합니다.
- 동물·보호센터 정보는 검색 API(레포 루트 `scripts/serve/run_api`, 기본 http://localhost:8000)에서 옵니다. 주소는 `.env.local`의 `NEXT_PUBLIC_API_BASE_URL`로 바꿉니다.
- 매칭 점수·이유·거리는 아직 API에 없어 화면에서 숨겨집니다(`Animal`의 선택 필드). 문장 검색은 글 벡터, 사진 검색은 사진 벡터로 순서를 정합니다(db 모드. sample 모드는 문장을 단어 일치로 거르고 사진 검색은 503).
- 공고 사진은 칸 크기를 고정하고 `object-fit: contain`으로 전체를 보입니다(`cover`로 자르면 얼굴이 잘림). 남는 곳은 칸 배경색(`a.bg`)입니다.
- 사진 태그 검색: `TagPicker`는 먼저 강아지·고양이를 고르게 하고(생활 조건의 종류와 같은 값), 그 종의 태그 세트 `/tags?species=dog|cat`(범주별 상세 태그·추천 태그)를 보여 줍니다. 고양이 태그 세트는 준비 중이라 비어 있고, 종별 태그 세트는 API `serving/tags.py`의 `TAG_SETS` 표 하나로 정합니다. 종류가 전체(처음 상태)면 종 선택과 안내만 보이고, 종을 고르면 그 종의 태그가 나타납니다. 종류가 바뀌면 고른 태그는 지워집니다. 화면은 고른 태그를 `/animals?tags=`로 보냅니다. 태그는 거르지 않고 순서만 정합니다(모두 맞음 → 일부 맞음 → 태그 정보 없음 → 다른 태그, 응답 `tagMatch`·`matched`). 털이 고른 것과 반대인 아이만 빠집니다. 태그를 누르면 마지막으로 제출한 문장으로 바로 다시 검색합니다. sample 모드 태그는 `data/sample/photo_tags.json`(실제 태거 결과)이며 `/tags`를 받지 못하면 태그 영역을 숨깁니다.

## 휴대폰에서 테스트 (같은 Wi-Fi)

PC의 LAN IP를 `ipconfig`로 확인합니다 (예: `192.168.0.10`).

1. API: `.\scripts\serve\run_api.ps1 -BindHost 0.0.0.0` (bash는 `HOST=0.0.0.0 scripts/serve/run_api.sh`)
2. 루트 `.env`의 `REBOM_CORS_ORIGINS`에 `http://192.168.0.10:3000` 추가. 빠지면 화면은 뜨지만 데이터가 비어 보입니다.
3. `frontend/.env.local`에 `NEXT_PUBLIC_API_BASE_URL=http://192.168.0.10:8000`
4. `pnpm dev:lan` 후 폰에서 `http://192.168.0.10:3000` 접속
5. 안 열리면 Windows 방화벽에서 3000, 8000 포트 인바운드를 허용합니다.

<!-- BEGIN:nextjs-agent-rules -->

## This is NOT the Next.js you know

This version has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` (resolved from this file's directory; in monorepos the `next` package may not be visible from the repo root) before writing any code. Heed deprecation notices.

This block is written and re-added by `next dev` — verify at `node_modules/next/dist/server/lib/generate-agent-files.js`. Removing it from a diff only re-creates the uncommitted change; committing it with your work keeps the tree clean.

<!-- END:nextjs-agent-rules -->
