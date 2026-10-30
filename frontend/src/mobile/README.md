# 앱 최적화 화면 (mobile/)

설치형 네이티브 앱이 아닌, 같은 서비스의 모바일 앱 UX를 확인하는 웹 프로토타입입니다.
앱 모드(`ViewMode = 'app'`)에서만 쓰이는 화면 구성과 배치를 이 폴더에 둡니다.

- `layout/`: 앱 헤더(`MobileHeader`), 하단 탭(`MobileTabs`), 넓은 화면용 안내 패널(`AppPreviewAside`)
- `screens/MobileSearchScreen.tsx`: 앱 입양 찾기 화면 배치
- `components/`: 모바일 검색 박스, 추천 문장, 결과 목록, 세로형 동물 카드
- `styles/mobile.css`: 앱 영역에만 적용되는 레이아웃·터치·안전 영역·바텀시트 스타일
- 검색어, 첨부 사진, 조건, 정렬, 페이지 상태는 `src/providers/SearchProvider.tsx`의 공통 상태를 사용합니다.
- 동물 상세·입양 안내·보호센터·서비스 소개 본문은 `src/screens/`를 웹과 함께 사용합니다.
  앱 모드에서는 `compact`로 상세 정보를 접어 보고, 하단 고정 문의 버튼과 목록/지도 전환을 사용합니다.
- 웹/앱 전환 시 같은 본문 컴포넌트를 유지하므로 안내 단계, 지역 선택, 체크리스트 상태도 유지됩니다.
  화면별 스크롤 위치는 모드별로 보관합니다 (`src/hooks/useScrollMemory.ts`).
