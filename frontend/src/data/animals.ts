import type { Animal } from '@/types/animal'

/** 첫 화면에 보여줄 예시 동물 (가상 데이터) */
export const animals: Animal[] = [
  { id: 1, kind: '믹스견', gender: '수컷', age: 3, kg: 11, color: '갈색', care: '중성화 완료', score: 94, available: false, center: '봄빛구 동물보호센터', distance: 4.2, quote: '사람을 잘 따르고 짖음이 거의 없음', reasons: [['조용함', '문장'], ['산책 좋아함', '문장'], ['사진과 닮음', '사진']], coat: '#CA945F', ear: '#9B653A', bg: '#F7E7D9' },
  { id: 2, kind: '믹스견', gender: '암컷', age: 5, kg: 9, color: '크림색', care: '중성화 완료', score: 89, available: true, center: '다온시 동물보호센터', distance: 12.8, quote: '실내 배변 가능, 산책할 때 줄을 거의 당기지 않음', reasons: [['차분함', '문장'], ['중형', '조건'], ['사진과 닮음', '사진']], coat: '#EAD3AE', ear: '#D4B68A', bg: '#E9EDDD' },
  { id: 3, kind: '진도 믹스', gender: '수컷', age: 2, kg: 14, color: '흰색', care: '중성화 예정', score: 85, available: false, center: '푸른군 동물보호센터', distance: 27.5, quote: '활발하지만 다른 개와 잘 지냄', reasons: [['산책 좋아함', '문장'], ['2~7살', '조건']], coat: '#FAF8F0', ear: '#EEE9DB', bg: '#DEE6D0' },
  { id: 4, kind: '믹스견', gender: '암컷', age: 7, kg: 10, color: '검정·갈색', care: '건강검진 완료', score: 82, available: true, center: '온유구 동물보호센터', distance: 8.1, quote: '사람 품을 좋아하고 낯선 곳에서도 금방 편안해짐', reasons: [['조용함', '문장'], ['사진과 닮음', '사진']], coat: '#494039', ear: '#302C28', bg: '#FAE3D1' },
  { id: 5, kind: '믹스견', gender: '수컷', age: 4, kg: 12, color: '연갈색', care: '중성화 완료', score: 80, available: true, center: '해솔시 동물보호센터', distance: 18.6, quote: '천천히 다가가면 꼬리를 흔들며 반겨 줌', reasons: [['차분함', '문장'], ['중형', '조건']], coat: '#D1B08C', ear: '#A78160', bg: '#E8E7DF' },
  { id: 6, kind: '믹스견', gender: '암컷', age: 3, kg: 6, color: '흰색·갈색', care: '중성화 완료', score: 78, available: false, center: '늘봄구 동물보호센터', distance: 22.3, quote: '장난감을 좋아하고 기본적인 기다려가 가능함', reasons: [['산책 좋아함', '문장'], ['소형', '조건']], coat: '#F1E6D4', ear: '#B88962', bg: '#F2EADF' },
  { id: 7, kind: '코리안숏헤어', gender: '암컷', age: 2, kg: 4, color: '치즈색', care: '중성화 완료', score: 76, available: true, center: '새봄시 동물보호센터', distance: 16.2, quote: '창가에서 쉬는 시간을 좋아하는 다정한 아이', reasons: [['조용함', '문장'], ['2~7살', '조건']], coat: '#DCA76A', ear: '#C68D52', bg: '#E9EDDF', cat: true },
  { id: 8, kind: '믹스견', gender: '수컷', age: 6, kg: 13, color: '초콜릿색', care: '중성화 완료', score: 74, available: true, center: '한울구 동물보호센터', distance: 31.4, quote: '느긋한 산책과 사람 곁에서 쉬는 것을 좋아함', reasons: [['차분함', '문장'], ['중형', '조건']], coat: '#96725A', ear: '#71523F', bg: '#E4E8D9' },
]

const matchingExamples = animals.filter(a => !a.cat && a.kg >= 8)

/** 검색 결과용 전체 공고: 기본 8마리 + 가상 생성 18마리 */
export const searchAnimals: Animal[] = [
  ...animals,
  ...Array.from({ length: 18 }, (_, i) => ({
    ...matchingExamples[i % matchingExamples.length],
    id: i + 9,
    score: 73 - i,
    center: `${['마루', '아람', '도담', '소담', '라온', '여울'][i % 6]}${['시', '구', '군'][Math.floor(i / 6)]} 동물보호센터`,
    distance: Math.round((33 + i * 2.7) * 10) / 10,
  })),
]

export function findAnimal(id: number) {
  return searchAnimals.find(a => a.id === id)
}
