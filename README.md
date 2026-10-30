# Re:봄
[SKP AI DE 1기] 유기동물 멀티모달 검색 프로젝트

문장 · 사진 · 생활 조건으로 전국 보호소의 유기동물을 검색하고, 잘 맞는 친구를 찾아 보호소 방문과 입양 상담으로 이어 주는 입양 매칭 서비스입니다.

## 저장소 구성

```
re-bom/
├─ frontend/                 # 웹·앱 화면 (Next.js)
└─ .github/workflows/        # PR 알림(Discord) 워크플로
```

---

## Frontend

> 현재 프론트엔드는 **가상 데이터로 동작하는 프로토타입**입니다. 서버, 문장·사진 AI 분석, 실제 예약은 연결되어 있지 않습니다.

### 기술 스택

| 구분 | 사용 기술 |
|---|---|
| 프레임워크 | Next.js 16 (App Router) |
| UI | React 19 |
| 스타일 | Tailwind CSS v4 (`@tailwindcss/postcss`) + CSS 파일 |
| 언어 | TypeScript 5.9 (strict), 경로 별칭 `@/*` → `src/*` |
| 린트·포맷 | ESLint 9 + eslint-config-next, oxfmt |
| 패키지 매니저 | pnpm |
| 런타임 | Node.js 20.9 이상 (`.mise.toml`) |

### 실행 방법

```bash
cd frontend
pnpm install
pnpm dev        # 개발 서버 (http://localhost:3000, PORT 환경 변수로 변경 가능)
pnpm build      # 프로덕션 빌드 (모든 화면 정적 생성)
pnpm start      # 빌드 결과 실행
pnpm lint       # ESLint
pnpm typecheck  # 타입 검사
```

### 화면과 경로

| 경로 | 화면 | 설명 |
|---|---|---|
| `/` | 입양 찾기 | 문장·사진·조건 검색, 매칭 결과(목록/지도), 정렬, 보호센터별 보기 |
| `/animals/[id]` | 동물 상세 | 매칭 이유, 입양 가능 시점, 공고·외형·성격·건강·보호소 정보, 지역 지원, 비슷한 아이들 |
| `/guide` | 입양 절차 안내 | 입양 6단계, 지역별 지원 예시, 입양 행사, 자주 묻는 질문, 방문 전 체크리스트 |
| `/shelters` | 보호센터 | 지역·운영 형태 필터, 목록과 예시 지도, 센터의 아이들 보기 |
| `/about` | 서비스 소개 | 서비스 소개, 통계, 동작 방식, 데이터 원칙 |

### 웹 / 앱 미리보기

화면 맨 위의 토글로 **웹 화면**(넓은 화면)과 **앱 화면**(모바일 프레임)을 전환할 수 있습니다.

- 검색어, 첨부 사진, 조건, 정렬과 보고 있는 화면은 전환해도 그대로 유지됩니다.
- 화면별 스크롤 위치는 모드별로 따로 기억합니다.
- 입양 찾기 화면은 웹과 앱의 배치가 크게 달라 따로 구현했습니다. 나머지 화면은 같은 본문을 쓰고, 앱에서는 정보를 접어 보고 하단에 고정된 문의 버튼을 사용합니다.
- 설치형 네이티브 앱이 아니라, 모바일 앱 UX를 확인하는 웹 프로토타입입니다.

### 폴더 구조

```
frontend/
├─ public/
│  ├─ fonts/             # Noto Sans KR 가변 WOFF2 (유니코드 범위별 분할, OFL.txt 포함)
│  ├─ mascots/           # 마스코트 이미지 (WebP)
│  └─ favicon.svg
└─ src/
   ├─ app/               # Next.js 라우트 (page.tsx는 화면 컴포넌트만 렌더)
   │  ├─ layout.tsx      # <html>, 전역 CSS, Providers, ExperienceShell
   │  ├─ page.tsx                 # /
   │  ├─ animals/[id]/page.tsx    # /animals/:id
   │  ├─ guide/  shelters/  about/
   │  └─ not-found.tsx
   │
   ├─ web/               # 웹 전용 화면 구성·배치
   │  ├─ layout/         # WebHeader
   │  ├─ screens/        # WebSearchScreen
   │  ├─ components/     # SearchCard, SearchSuggestions, SearchResults, ResultMap, CenterFilterBanner
   │  └─ styles/         # header.css, search.css
   │
   ├─ mobile/            # 앱 전용 화면 구성·배치
   │  ├─ layout/         # MobileHeader, MobileTabs, AppPreviewAside
   │  ├─ screens/        # MobileSearchScreen
   │  ├─ components/     # MobileHero, MobileSearchForm, MobileSuggestions, MobileResults, MobilePetCard
   │  └─ styles/         # mobile.css
   │
   │  ── 이하 웹·앱 공통 ──
   ├─ screens/           # 웹·앱이 함께 쓰는 화면 본문
   │  ├─ search/         # SearchScreen: 모드에 따라 web/mobile 화면 선택
   │  ├─ animal-detail/  # AnimalDetail, PetGallery, ProfilePanel, DetailInformation, DetailModal
   │  ├─ adoption-guide/ # AdoptionGuide
   │  ├─ shelter-directory/
   │  ├─ about/
   │  └─ common/         # Checklist, SupportCards, RegionSelect
   ├─ components/        # 공통 UI
   │  ├─ layout/         # ExperienceShell(웹/앱 틀 조립), PreviewControls, SiteFooter
   │  ├─ animal/         # AnimalArt, PetCard, Reasons
   │  ├─ mascot/         # MascotArt, HeroMascot, ThemeToggle, MascotState, MascotDialog
   │  ├─ search/         # FilterDialog
   │  ├─ map/  icons/  ui/
   ├─ providers/         # ViewMode · Search · Mascot · Experience(화면 이동·스크롤) Context
   ├─ hooks/             # useScrollMemory
   ├─ data/              # 가상 데이터·상수 (animals, shelters, filters, adoption-guide, mascots)
   ├─ lib/               # 순수 로직 (search: 필터·정렬, animal: 공고번호·태그·비슷한 아이들)
   ├─ constants/         # routes (경로, 주 메뉴)
   ├─ types/             # Animal, Shelter, Filters, ViewMode
   └─ styles/            # globals.css(진입점), tokens, fonts, 공통 CSS
```

### 코드 작성 규칙

- **웹 또는 앱 한쪽에서만** 쓰는 화면 배치·컴포넌트 → `src/web/` 또는 `src/mobile/`
- **양쪽이 함께** 쓰는 화면 본문 → `src/screens/`, 작은 UI 조각 → `src/components/`
- **새 페이지** → `src/app/<경로>/page.tsx`는 화면 컴포넌트만 렌더하고, 로직은 `src/screens/`에 둡니다.
- **화면 이동**은 `useExperience().navigate(href)`를 사용합니다 (스크롤 복원 포함).
- **검색 상태**는 `useSearch()`(SearchProvider)에서 읽고 바꿉니다.
- **실제 API 연동**은 `src/types/`의 데이터 모양을 기준으로 `src/data/`의 가상 데이터를 바꿔 넣습니다.

### 스타일

- `src/styles/globals.css`가 유일한 CSS 진입점이며 모든 CSS 파일을 `@import`합니다.
  **import 순서가 곧 CSS 적용(cascade) 순서**이므로 순서를 바꾸지 마세요.
- 디자인 토큰은 `src/styles/tokens.css`의 Tailwind v4 `@theme`에 있습니다.
  (`--color-forest`, `--color-peach`, `--color-sage`, `--color-cream` 등)
- 앱 전용 스타일은 `.app-preview-mode` / `.mobile-app` 범위 안에서만 적용됩니다.
- 한글 글꼴은 `src/styles/fonts.css`의 `@font-face`가 `public/fonts/`의 Noto Sans KR을 필요한 유니코드 범위만 불러옵니다.

### 마스코트

| 이름 | 동물 | 성격 |
|---|---|---|
| 리멍이 (`remeong-*`) | 강아지 | 다정함 · 차분함 · 따뜻한 응원 |
| 봄냥이 (`bomnyang-*`) | 고양이 · 코리안 숏헤어 | 호기심 · 다정함 · 사람과 동물을 좋아함 |

- 헤더의 테마 토글은 화면의 분위기(장식·강조 색)만 바꾸며, 검색 조건은 바꾸지 않습니다.
- 화면에서는 portrait, greeting, basic, curious, cheer, rest, sleep, walk, run, empty 포즈를 사용하고,
  나머지 이미지(happy, love, sit 등)는 이후 화면에 쓰려고 보관 중입니다. 자세한 내용은 `frontend/public/mascots/README.md`를 참고하세요.

### 데이터 출처 및 고지

- 공고 원본: 농림축산식품부 국가동물보호정보시스템 (공공데이터포털)
- 통계: 농림축산식품부 반려동물 보호·복지 실태조사(2025)
- 현재 화면의 동물·보호센터·행사·지원금 정보는 모두 **가상 예시**입니다.
